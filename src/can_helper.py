import os
import asyncio
import logging
import subprocess
import paho.mqtt.client as mqtt
import canopen
import pyudev

# --- Constants ---
PROVISIONAL_NODE_ID = 127
SDO_NODE_ID_INDEX = 0x2002
DEFAULT_TOPIC_PREFIX = 'can_helper'

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class CanHelper:
    def __init__(self):
        # System Config
        self.can_interface = os.environ.get('CAN_INTERFACE', 'can0')
        self.bitrate = os.environ.get('BITRATE', '125000')

        # MQTT Config
        self.topic_prefix = os.environ.get('TOPIC_PREFIX', DEFAULT_TOPIC_PREFIX)
        self.mqtt_host = os.environ.get('MQTT_HOST')
        self.mqtt_port = int(os.environ.get('MQTT_PORT', 1883))
        self.mqtt_user = os.environ.get('MQTT_USER')
        self.mqtt_password = os.environ.get('MQTT_PASSWORD')
        self.mqtt_client = None

        # CANopen network
        self.network = None

        # State management
        self.provisioning_command = None
        self.specific_id_to_assign = None
        self.is_provisioning = asyncio.Lock()

    def _ensure_can_interface_up(self):
        """Attempts to configure the CAN interface by bringing it down, setting type/bitrate, and bringing it up."""
        device = self.can_interface
        bitrate = self.bitrate

        try:
            # Always attempt to bring the interface down first to ensure a clean state for configuration.
            logging.info(f"Attempting to ensure CAN interface '{device}' is down for reconfiguration.")
            try:
                subprocess.run(['ip', 'link', 'set', device, 'down'], check=True, capture_output=True)
                logging.info(f"CAN interface '{device}' successfully brought down (or was already down).")
            except subprocess.CalledProcessError as e:
                logging.warning(f"Could not bring down CAN interface '{device}' (Error: {e.stderr.strip() if e.stderr else e}). "
                                f"This might indicate the interface does not exist or is in an unmanageable state. "
                                "Attempting to proceed with configuration.")
                # Do not re-raise, try to proceed with config.

            # Configure type and bitrate
            logging.info(f"Setting type 'can' and bitrate {bitrate} for CAN interface '{device}'.")
            subprocess.run(
                ['ip', 'link', 'set', device, 'type', 'can', 'bitrate', bitrate],
                check=True,
                capture_output=True
            )
            logging.info(f"Successfully set type 'can' and bitrate for CAN interface '{device}'.")

            # Bring up the interface
            logging.info(f"Bringing up CAN interface '{device}'.")
            subprocess.run(
                ['ip', 'link', 'set', device, 'up'],
                check=True,
                capture_output=True
            )
            logging.info(f"Successfully brought up CAN interface '{device}'.")
                
            return True

        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to configure CAN interface '{device}'. Error: {e.stderr.strip() if e.stderr else e}.")
            return False
        except FileNotFoundError:
            logging.error("Required command or file not found (e.g., 'ip' command). Ensure 'iproute2' is installed and system paths are accessible.")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred while managing CAN interface '{device}': {e}")
            return False

    def on_mqtt_connect(self, client, userdata, flags, rc, properties):
        logging.info("Connected to MQTT broker.")
        command_topic = f"{self.topic_prefix}/command"
        specific_id_topic = f"{self.topic_prefix}/specific_id"
        client.subscribe([(command_topic, 0), (specific_id_topic, 0)])
        logging.info(f"Subscribed to '{command_topic}' and '{specific_id_topic}'")

    def on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        logging.info(f"Received MQTT message on topic '{msg.topic}': {payload}")

        if msg.topic == f"{self.topic_prefix}/command":
            if payload in ['provision_next_free', 'provision_specific']:
                self.provisioning_command = payload
                asyncio.create_task(self.run_provisioning())
            else:
                logging.warning(f"Unknown command received: {payload}")
        
        elif msg.topic == f"{self.topic_prefix}/specific_id":
            try:
                self.specific_id_to_assign = int(payload)
                logging.info(f"Specific ID to assign set to: {self.specific_id_to_assign}")
            except ValueError:
                logging.error(f"Invalid specific ID received: {payload}")

    async def find_next_free_id(self):
        logging.info("Scanning network for used IDs...")
        self.network.scanner.scan()
        await asyncio.sleep(2)
        used_ids = set(self.network.scanner.nodes)
        logging.info(f"Used IDs: {used_ids}")
        for i in range(1, PROVISIONAL_NODE_ID):
            if i not in used_ids:
                logging.info(f"First free ID is {i}")
                return i
        return None

    async def run_provisioning(self):
        """The core provisioning logic, triggered by MQTT."""
        async with self.is_provisioning:
            if not self.provisioning_command: return
            command = self.provisioning_command
            self.provisioning_command = None

            if not self.network or not self.network.bus:
                 logging.error("CAN bus not connected. Cannot run provisioning.")
                 self.mqtt_client.publish(f"{self.topic_prefix}/status", "Error: CAN bus not connected.")
                 return
            
            self.mqtt_client.publish(f"{self.topic_prefix}/status", f"Starting provisioning: {command}")
            
            target_id = None
            if command == 'provision_specific':
                if self.specific_id_to_assign is None:
                    self.mqtt_client.publish(f"{self.topic_prefix}/status", "Error: Specific ID not set.")
                    return
                target_id = self.specific_id_to_assign
            elif command == 'provision_next_free':
                target_id = await self.find_next_free_id()
                if not target_id:
                    self.mqtt_client.publish(f"{self.topic_prefix}/status", "Error: No free IDs available.")
                    return
            
            new_node_detected = asyncio.Event()
            def new_node_callback(node_id):
                if node_id == PROVISIONAL_NODE_ID:
                    logging.info(f"Detected unconfigured device with ID {PROVISIONAL_NODE_ID}!")
                    new_node_detected.set()

            self.network.scanner.add_callback(new_node_callback)
            
            try:
                logging.info(f"Waiting for a new device to appear with ID {PROVISIONAL_NODE_ID}...")
                await asyncio.wait_for(new_node_detected.wait(), timeout=60)
                
                logging.info(f"Attempting to assign new ID {target_id}...")
                node = self.network.add_node(PROVISIONAL_NODE_ID, None)
                await node.sdo.download(SDO_NODE_ID_INDEX, 0, target_id.to_bytes(1, 'little'))
                
                status_msg = f"Success! Assigned ID {target_id} to new device."
                logging.info(status_msg)
                self.mqtt_client.publish(f"{self.topic_prefix}/status", status_msg)
            except asyncio.TimeoutError:
                self.mqtt_client.publish(f"{self.topic_prefix}/status", "Timeout: No new device found.")
            except Exception as e:
                self.mqtt_client.publish(f"{self.topic_prefix}/status", f"Error: {e}")
            finally:
                self.network.scanner.remove_callback(new_node_callback)

    def _handle_udev_event(self):
        """Callback executed when the asyncio loop detects activity on the udev monitor."""
        try:
            device = self.udev_monitor.receive_device()
            if device and device.sys_name == self.can_interface:
                logging.info(f"Udev event received for '{device.sys_name}': {device.action}")
                if device.action in ('add', 'change'):
                    if self._ensure_can_interface_up() and (not self.network or not self.network.bus):
                        self.connect_can()
        except Exception as e:
            logging.error(f"Error processing udev event: {e}")

    def connect_can(self):
        try:
            logging.info("Connecting to CAN bus...")
            if self.network and self.network.bus:
                self.network.disconnect()
            self.network = canopen.Network()
            self.network.connect(bustype='socketcan', channel=self.can_interface)
            logging.info("CAN bus connected.")
        except Exception as e:
            logging.error(f"Failed to connect to CAN bus: {e}")

    async def run(self):
        """Main, long-running service loop driven by udev and MQTT events."""
        logging.info("Initializing CAN Helper service...")
        
        # Initial setup
        if self._ensure_can_interface_up():
            self.connect_can()

        # Setup MQTT
        self.mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        if self.mqtt_user and self.mqtt_password:
            self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_password)
        self.mqtt_client.connect_async(self.mqtt_host, self.mqtt_port, 60)
        self.mqtt_client.loop_start()

        # Setup UDEV monitor
        context = pyudev.Context()
        self.udev_monitor = pyudev.Monitor.from_netlink(context)
        self.udev_monitor.filter_by(subsystem='net')
        
        loop = asyncio.get_running_loop()
        loop.add_reader(self.udev_monitor.fileno(), self._handle_udev_event)
        
        logging.info("Udev monitor started. Service is running.")
        
        # Keep the main coroutine alive indefinitely
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    helper = CanHelper()
    asyncio.run(helper.run())
