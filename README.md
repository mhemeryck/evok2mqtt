# evok2mqtt

Query the evok unipi API server via websockets and push out MQTT messages from it.

## Run as a service in systemd

Example systemd service file

	[Unit]
	Description=evok2mqtt
	After=network-online.target

	[Service]
	Type=simple
	User=%i
	ExecStart=/srv/evok2mqtt/bin/evok2mqtt <host>
	Restart=on-failure
	RestartSec=5s

	[Install]
	WantedBy=multi-user.target
