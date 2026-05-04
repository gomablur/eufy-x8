DOMAIN = "eufy_x8"

# Eufy auth
EUFY_LOGIN_URL = "https://home-api.eufylife.com/v1/user/email/login"
EUFY_USER_AGENT = "EufyHome-Android-3.1.3-753"

# DPS numbers (Eufy X8, Tuya v3.3 local protocol)
DPS_POWER           = "1"    # bool
DPS_ACTIVATE        = "2"    # bool: start/stop clean
DPS_WORK_MODE       = "5"    # str: "auto" | "Nosweep" | "Edge" | "Spot"
DPS_WORK_STATUS     = "15"   # str: see WORK_STATUS_* below
DPS_ERROR_CODE      = "106"  # int/str: error code (0 = no error)
DPS_DO_NOT_DISTURB  = "107"  # bool: Do Not Disturb mode
DPS_RETURN_HOME     = "101"  # bool: True = go to dock
DPS_CLEAN_SPEED     = "102"  # str: see FAN_SPEED_* below
DPS_LOCATE          = "103"  # bool: toggle beeper
DPS_BATTERY         = "104"  # int: 0-100
DPS_CLEANING_TIME   = "109"  # int: seconds
DPS_CLEANING_AREA   = "110"  # int: m²
DPS_CONSUMABLES     = "116"  # str: base64 JSON consumable durations
DPS_BOOST_IQ        = "118"  # bool: BoostIQ auto-boost on carpet
DPS_WORK_STATUS_2   = "122"  # str: granular status
DPS_COMMAND_TRANS   = "124"  # str: base64 JSON command transport
DPS_MAP_INFO        = "125"  # str: base64 JSON map info
DPS_AUTO_RETURN     = "135"  # bool: auto-return to dock after clean
DPS_LAST_CLEAN      = "142"  # str: base64 JSON last clean result

# DPS 15 work status values
WORK_STATUS_SLEEPING  = "Sleeping"
WORK_STATUS_RUNNING   = "Running"
WORK_STATUS_CHARGING  = "Charging"
WORK_STATUS_LOCATING  = "Locating"
WORK_STATUS_RECHARGE  = "Recharge"
WORK_STATUS_COMPLETED = "Completed"
WORK_STATUS_STANDBY   = "standby"
WORK_STATUS_GOTO      = "Goto"

# DPS 102 fan speed values
FAN_SPEED_QUIET    = "Quiet"
FAN_SPEED_STANDARD = "Standard"
FAN_SPEED_TURBO    = "Turbo"
FAN_SPEED_MAX      = "Max"

# DPS 102 values as sent to/received from the device
FAN_SPEEDS = [FAN_SPEED_QUIET, FAN_SPEED_STANDARD, FAN_SPEED_TURBO, FAN_SPEED_MAX]

# X series (T2262 / T2262EV): device sends "Pure" for the lowest speed
# Map to human-friendly labels for HA
FAN_SPEED_TO_LABEL = {
    "Pure":     "Low",
    "Standard": "Medium",
    "Turbo":    "High",
    "Max":      "Max",
}
FAN_SPEED_FROM_LABEL = {v: k for k, v in FAN_SPEED_TO_LABEL.items()}
FAN_SPEED_LABELS = list(FAN_SPEED_TO_LABEL.values())  # ["Low", "Medium", "High", "Max"]

# HA vacuum activity mapping
ACTIVITY_MAP = {
    WORK_STATUS_SLEEPING:  "docked",
    WORK_STATUS_CHARGING:  "docked",
    WORK_STATUS_RUNNING:   "cleaning",
    WORK_STATUS_RECHARGE:  "returning",
    WORK_STATUS_COMPLETED: "idle",
    WORK_STATUS_STANDBY:   "idle",
    WORK_STATUS_GOTO:      "cleaning",
    WORK_STATUS_LOCATING:  "idle",
}

# Goto coordinate system (DPS 124)
#
# Goto coordinates are in the robot's persistent SLAM map coordinate system.
# They are stable across sessions and can be used directly in DPS 124 goto commands.
#
# The media.latest v3.0 "path data" API returns session-LOCAL coordinates, not
# map coordinates. Each session (clean, goto, spot) has its own local origin,
# so v3.0 coordinates cannot be reliably converted to goto coordinates.
#
# Known goto coordinates (downstairs T2262, captured via ARP intercept):
#   Bin:  goto=(2283, -363)   [confirmed 2026-05-03]
#   Dock: goto≈(1853, -295)   [estimated; dock is start of every session]
#
# To find goto coords for a new location: use standalone/intercept_goto.py
# while the Eufy app sends the robot there.

# Config entry keys
CONF_EMAIL    = "email"
CONF_PASSWORD = "password"
CONF_DEVICE_ID   = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_IP   = "device_ip"
CONF_LOCAL_KEY   = "local_key"

# Coordinator update interval (seconds)
UPDATE_INTERVAL = 30
