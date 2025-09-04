# DUS Signin Plugin

This is a DUS signin plugin developed for AstrBot, supporting manual signin, scheduled signin, and signin result notification features.

## Features

1. **Signin Configuration** - Support setting Cookie, latitude/longitude coordinates, class ID and other signin parameters
2. **Immediate Signin** - Execute signin immediately according to configuration
3. **Scheduled Signin** - Support daily scheduled automatic signin
4. **Signin Notifications** - Configurable signin result notification levels (always/never/failure_only)

## Usage

### Configuration Commands

```
/signin set cookie <value>           # Set login cookie (required)
/signin set lat <value>              # Set latitude coordinate (required)
/signin set lng <value>              # Set longitude coordinate (required)
/signin set class_id <value>         # Set class ID (optional, auto-fetch)
/signin set offset <value>           # Set GPS coordinate offset (default: 0.000020)
/signin set auto_time <HH:MM>        # Set auto signin time
/signin set auto_enable <enable/disable> # Enable/disable auto signin
/signin set notification <level>     # Set notification level for current chat
/signin set remove_notification     # Remove notification settings for current chat
```

### Function Commands

```
/signin now                         # Execute signin immediately
/signin config                      # View current configuration
/signin help                        # Show help information
```

## Notification Features

### Multi-Chat Notification Support
- **Private Chat Notifications**: Recommended to set to "always" for real-time signin status updates
- **Group Chat Notifications**: Recommended to set to "failure_only" for group members to remind on failures
- **Flexible Configuration**: Different notification levels can be set for different chats without interference

### Usage Examples
```
# Set always notification in private chat
/signin set notification always

# Set failure_only notification in group chat
/signin set notification failure_only

# Remove notification settings for current chat
/signin set remove_notification
```

## GPS Offset Feature

The plugin supports GPS coordinate offset to add random variations to your location coordinates:

- **Default Offset**: 0.000020 (approximately 2 meters)
- **Range**: Random offset between -offset and +offset is applied to both latitude and longitude
- **Purpose**: Helps avoid detection by adding slight randomness to your GPS coordinates
- **Configuration**: Use `/signin set offset <value>` to set custom offset value

### Example:
```
/signin set offset 0.000030    # Set offset to ±30 meters approximately
/signin set offset 0.000010    # Set offset to ±10 meters approximately
/signin set offset 0          # Disable offset (use exact coordinates)
```

## Notes

1. Cookie, latitude, longitude are required parameters, can be obtained from browser developer tools
2. Class ID will auto-fetch class list when empty
3. Support multi-chat notifications, each chat can set different notification levels
4. Notification levels: always/never/failure_only
5. GPS offset adds random variation to coordinates for better privacy

## Dependencies

```bash
pip install aiohttp>=3.8.0
```

## Key Features

- **Strict HTTP Request Specification** - Strict implementation based on `signin.sh` script
- **Smart Class Recognition** - Automatic class fetching and selection
- **Flexible Notification System** - Support multiple notification levels
- **Persistent Configuration** - User configuration auto-save and recovery
- **Scheduled Task Management** - Reliable scheduled signin mechanism

## Technical Implementation

The plugin is based on AstrBot plugin development framework, uses aiohttp for HTTP requests, supports asynchronous operations and task scheduling. Strictly implements according to the original signin script's request headers and parameter requirements to ensure signin success rate.