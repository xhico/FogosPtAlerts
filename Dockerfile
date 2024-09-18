# Use the latest Python image from Docker Hub
FROM python:latest

# Install git and cron
RUN apt-get update && apt-get install -y git cron

# Set the working directory in the container
WORKDIR /root/FogosPtAlerts

# Copy the project files and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy 911
COPY 911_enc /root/.911

# Clone the Git repository and copy Misc.py
RUN git clone https://github.com/xhico/Misc.git /root/Misc/
RUN cp /root/Misc/Misc.py /root/FogosPtAlerts/

# Add the crontab file and ensure it's executable
COPY crontab /etc/cron.d/fogosptalerts-cron
RUN chmod 0644 /etc/cron.d/fogosptalerts-cron

# Apply cron job
RUN crontab /etc/cron.d/fogosptalerts-cron

# Ensure cron service logs are available
RUN touch /var/log/cron.log

# Command to run cron and keep the container running
CMD ["cron", "-f"]
