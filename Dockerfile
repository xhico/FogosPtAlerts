# Use the latest Python image from Docker Hub
FROM python:3.13-slim
LABEL maintainer="xhico"

# Clean and minimize image size — no extra packages needed
RUN apt-get update \
    && apt clean \
    && apt autoclean \
    && apt autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the project files and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all remaining application files
COPY . .