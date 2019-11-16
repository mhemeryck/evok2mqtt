from setuptools import setup

setup(
    name="evok2mqtt",
    version="0.1",
    description="Translate evok websocket messages to MQTT",
    url="https://github.com/mhemeryck/evok2mqtt",
    install_requires=("websockets>=8.1", "paho-mqtt>=1.5"),
    author="Martijn Hemeryck",
    license="MIT",
    packages=["evok2mqtt"],
    zip_safe=True,
    entry_points={"console_scripts": ["evok2mqtt=evok2mqtt:main"]},
)
