#!/bin/bash

python3 -m pip install -r /home/pi/FogosPtAlerts/requirements.txt --no-cache-dir
sudo mv /home/pi/FogosPtAlerts/FogosPtAlerts.service /etc/systemd/system/ && sudo systemctl daemon-reload
chmod +x -R /home/pi/FogosPtAlerts/*