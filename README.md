# UiHound

[English](README.md) | [简体中文](README.zh-CN.md)

UiHound is a local web tool for Android debugging and UI automation. Inspect device screens and UI elements, find element locators, remotely control devices, sample colors, capture screenshots and recordings, and analyze snapshots offline.

## Features

- **Inspect elements**: Browse the UI hierarchy and view element properties, coordinates, and screen highlights.
- **Find locators**: Search for and copy u2 selectors or XPath expressions.
- **Control devices**: View the live device screen and perform taps, swipes, long presses, Home, Back, and other actions.
- **Sample colors**: Inspect pixel coordinates and HEX, RGB, HSB, and HSV values.
- **Capture screenshots and recordings**: Save full-screen, element, and region screenshots, and record device screens.
- **Manage files**: Browse, copy, and manage screenshots and recordings.
- **Support multiple displays**: Select an Android display to inspect and control its screen.
- **Analyze offline**: Import an image and UI XML to inspect elements without a connected device.

## Requirements

- Python 3.10 or later.
- Android Platform Tools, with `adb` available on your `PATH`.
- An Android device with USB or wireless debugging enabled and authorized.
- macOS, Windows, or Linux. A recent version of Chrome or Edge is recommended.

Before connecting a device, verify that ADB can detect it:

```shell
adb devices
```

The device status should be `device`. For wireless debugging, connect first:

```shell
adb connect HOST:PORT
```

## Install and run

Install UiHound from PyPI and start it:

```shell
pip install uihound
uihound
```

The service opens a browser at `http://127.0.0.1:8000` by default. Select or enter an ADB device at the top of the page, then click **Connect Device**.

### Command-line options

```shell
# Choose a port
uihound --port 8080

# Do not open a browser automatically
uihound --no-browser

# Show the installed version
uihound --version
```

Upgrade to the latest version:

```shell
pip install --upgrade uihound
```

After upgrading, restart the UiHound service and refresh the page.

## Run from source

Clone the repository, then run these commands from its root directory:

```shell
python -m pip install .
uihound
```

## Network security

UiHound is intended for local debugging. Do not expose the service directly to an untrusted network.
