ARG BUILD_FROM=ghcr.io/home-assistant/base:12.1.0
FROM ${BUILD_FROM}

# Set up shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install can-utils and python
RUN apt-get update && apt-get install -y --no-install-recommends can-utils python3 python3-pip

# Create a directory for the application
WORKDIR /usr/src/app

# Copy the requirements file and install dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# This will be executed by run.sh
CMD [ "python3", "-u", "src/can_helper.py" ]

