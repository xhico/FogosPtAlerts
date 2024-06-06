#!/bin/bash

python3 -m venv /home/pi/FogosPtAlerts/venv
source /home/pi/FogosPtAlerts/venv/bin/activate
python3 -m pip install -r /home/pi/FogosPtAlerts/requirements.txt --no-cache-dir
chmod +x -R /home/pi/FogosPtAlerts/*
sudo mv /home/pi/FogosPtAlerts/FogosPtAlerts.service /etc/systemd/system/ && sudo systemctl daemon-reload

git clone https://github.com/xhico/Misc.git /home/pi/Misc
rsync -avp --progress /home/pi/Misc/Misc.py /home/pi/FogosPtAlerts/venv/lib/python$(python3 -c "import sys; print('{}.{}'.format(sys.version_info.major, sys.version_info.minor))")/site-packages/
rm -rf /home/pi/Misc/