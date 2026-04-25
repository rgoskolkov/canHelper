#!/usr/bin/env bashio

bashio::log.info "Starting CAN Helper service..."

# Export all configuration options as environment variables
export CAN_INTERFACE=$(bashio::config 'can_interface' 'can0')
export BITRATE=$(bashio::config 'bitrate' '125000')
export TOPIC_PREFIX=$(bashio::config 'provisioning_topic_prefix' 'can_helper')

# The 'services' functionality gives us these automatically
export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")

bashio::log.info "Configuration loaded:"
bashio::log.info "- CAN Interface: ${CAN_INTERFACE}"
bashio::log.info "- Bitrate: ${BITRATE}"
bashio::log.info "- MQTT Host: ${MQTT_HOST}"
bashio::log.info "- MQTT Topic Prefix: ${TOPIC_PREFIX}"

# Run the main python application
python3 -u /usr/src/app/src/can_helper.py
