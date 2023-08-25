#!/bin/bash

python3 -m pip install requests yagmail --no-cache-dir
sudo mv /home/pi/FogosPtAlerts/FogosPtAlerts.service /etc/systemd/system/ && sudo systemctl daemon-reload
chmod +x -R /home/pi/FogosPtAlerts/*