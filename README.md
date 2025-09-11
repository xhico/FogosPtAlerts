# FogosPT Alerts

## Overview

**FogosPT Alerts** is a Python-based monitoring tool that fetches live fire information from the [Fogos API](https://api-dev.fogos.pt/new/fires) and sends email notifications when new fires are detected, existing fires are updated, or fires are resolved. It is designed to run continuously, monitoring fire events around specified locations.

---

## Features

* Fetches live fire data from Fogos API.
* Compares live data with previously saved data to detect new, updated, or deleted entries.
* Sends email notifications for changes using an email-sending API.
* Customizable monitored locations and alert radius.
* Dockerized for easy deployment.

---

## Requirements

* Python 3.13+
* `requests` library
* `python-dotenv` for environment variable management
* Docker (optional, for containerized deployment)

---

## Setup

### 1. Clone the repository

```bash
git clone <repository_url>
cd FogosPTAlerts
```

### 2. Create a `.env` file

The script reads configuration from a `.env` file. Example:

```env
FOGOS_MAX_DISTANCE=50
FOGOS_CENTER_POINT_LAT=38.7169
FOGOS_CENTER_POINT_LONG=-9.1397
FOGOS_LOCATIONS=Lisbon,Sintra
FOGOS_EMAIL_SENDER_TO=youremail@example.com
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the script

```bash
python3 FogosPtAlerts.py
```

The script runs continuously, checking for fire updates every minute.

---

## Docker Deployment

### Dockerfile

The Dockerfile uses Python 3.13-slim, installs dependencies, and runs the script.

### docker-compose.yml

Example service configuration:

```yaml
services:
  fogosptalerts:
    container_name: "fogosptalerts"
    image: "fogosptalerts:latest"
    hostname: "fogosptalerts.docker"
    restart: "unless-stopped"
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
    env_file:
      - "stack.env"
    command: "python3 FogosPtAlerts.py"
```

### Build and run Docker container

```bash
docker build -t fogosptalerts .
docker-compose up -d
```

---

## Configuration

* **FOGOS\_MAX\_DISTANCE**: Maximum distance (km) from `CENTER_POINT` to monitor fires.
* **CENTER\_POINT\_LAT / CENTER\_POINT\_LONG**: Latitude and longitude for your monitoring center.
* **FOGOS\_LOCATIONS**: Comma-separated list of locations to monitor regardless of distance.
* **FOGOS\_EMAIL\_SENDER\_TO**: List of email addresses to receive alerts.
* **EMAIL\_SENDER\_API\_URL**: URL of the API endpoint to send emails.

---

## Logging

Logs are saved in the same directory as the script with the `.log` extension. The logs contain information about data fetching, differences detected, and emails sent.

---

## Notes

* Ensure the email-sending API is up and running.
* Make sure your `.env` file is correctly formatted.
* The script checks for updates every 1 minute by default.

---

## License

MIT License
