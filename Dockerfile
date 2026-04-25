FROM ghcr.io/home-assistant/base:latest

# Set up shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install can-utils and python
RUN apk add --no-cache can-utils python3 py3-pip

# Create a directory for the application
WORKDIR /usr/src/app

# Copy the requirements file and install dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copy the rest of the application
COPY . .

# This will be executed by run.sh
CMD [ "/run.sh" ]
