import sys
import argparse
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Optional, Dict, List, Any
import xml.etree.ElementTree as ET

try:
    import ftd2xx as ftd
except ImportError:
    print("Error: ftd2xx library not found. Install with: pip install ftd2xx")
    sys.exit(1)

try:
    import wx
except ImportError:
    print("Warning: wxPython not found. GUI mode will not be available.")
    print("Install with: pip install wxPython")
    wx = None


# ============================================================================
# ERROR CODES AND EXCEPTIONS
# ============================================================================

class ExitCode:
    """Standardized exit codes for CLI"""
    SUCCESS = 0
    GENERAL_ERROR = 1
    NO_DEVICES_FOUND = 2
    DEVICE_NOT_FOUND = 3
    CONNECTION_FAILED = 4
    COMMAND_EXECUTION_FAILED = 5
    INVALID_ARGUMENTS = 6
    DEVICE_DISCONNECTED = 7
    PERMISSION_DENIED = 8
    DEVICE_IN_USE = 9
    INVALID_RELAY_NUMBER = 10
    CONFLICTING_FLAGS = 11
    FTDI_DRIVER_ERROR = 12


class RelayControlException(Exception):
    """Base exception for relay control operations"""

    def __init__(self, message: str, exit_code: int):
        self.message = message
        self.exit_code = exit_code
        super().__init__(self.message)


class NoDevicesFoundError(RelayControlException):
    def __init__(self):
        super().__init__("No FTDI devices found", ExitCode.NO_DEVICES_FOUND)


class DeviceNotFoundError(RelayControlException):
    def __init__(self, identifier: str):
        super().__init__(f"Device '{identifier}' not found", ExitCode.DEVICE_NOT_FOUND)


class ConnectionFailedError(RelayControlException):
    def __init__(self, reason: str):
        super().__init__(f"Connection failed: {reason}", ExitCode.CONNECTION_FAILED)


class DeviceDisconnectedError(RelayControlException):
    def __init__(self):
        super().__init__("Device disconnected unexpectedly", ExitCode.DEVICE_DISCONNECTED)


class InvalidRelayNumberError(RelayControlException):
    def __init__(self, relay_num: Any):
        super().__init__(f"Invalid relay number: {relay_num}. Must be 1-4", ExitCode.INVALID_RELAY_NUMBER)


class ConflictingFlagsError(RelayControlException):
    def __init__(self, message: str):
        super().__init__(f"Conflicting flags: {message}", ExitCode.CONFLICTING_FLAGS)



# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

def setup_logging(log_level: str = 'INFO', log_to_file: bool = False,
                  log_file_path: Optional[Path] = None) -> logging.Logger:
    """
    Configure logging for the application.

    Args:
        log_level: DEBUG, INFO, WARNING, ERROR
        log_to_file: Whether to write logs to file
        log_file_path: Path to log file

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger('relay_control')
    logger.setLevel(getattr(logging, log_level))
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_to_file:
        if log_file_path is None:
            log_dir = Path.home() / '.relay_control'
            log_dir.mkdir(exist_ok=True)
            log_file_path = log_dir / 'relay_control.log'

        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5
        )
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file_path}")

    return logger


# ============================================================================
# SETTINGS MANAGEMENT
# ============================================================================

DEFAULT_SETTINGS = {
    'cli_quiet_mode': False,
    'cli_verbose_mode': False,
    'default_duration': 0.5,
    'log_level': 'INFO',
    'log_to_file': False,
    'log_file_path': None,
    'auto_disconnect_enabled': True,
    'auto_disconnect_timeout': 30,
    'auto_connect_startup': False,
    'last_device': None,
    'window_width': 800,
    'window_height': 600,
    'window_x': 100,
    'window_y': 100,
    'last_tab': 0,
}


def get_settings_path(custom_path: Optional[str] = None) -> Path:
    """Get path to settings file"""
    if custom_path:
        return Path(custom_path)

    settings_dir = Path.home() / '.relay_control'
    settings_dir.mkdir(exist_ok=True)
    return settings_dir / 'settings.xml'


def load_settings(custom_path: Optional[str] = None) -> Dict[str, Any]:
    """Load settings from XML, return defaults if missing"""
    settings = DEFAULT_SETTINGS.copy()
    settings_file = get_settings_path(custom_path)

    if not settings_file.exists():
        return settings

    try:
        tree = ET.parse(settings_file)
        root = tree.getroot()

        # Parse CLI settings
        cli = root.find('CLI')
        if cli is not None:
            settings['cli_quiet_mode'] = cli.findtext('QuietMode', 'false').lower() == 'true'
            settings['cli_verbose_mode'] = cli.findtext('VerboseMode', 'false').lower() == 'true'
            settings['default_duration'] = float(cli.findtext('DefaultDuration', '0.5'))
            settings['log_level'] = cli.findtext('LogLevel', 'INFO')
            settings['log_to_file'] = cli.findtext('LogToFile', 'false').lower() == 'true'
            log_path = cli.findtext('LogFilePath')
            settings['log_file_path'] = Path(log_path) if log_path else None

        # Parse Connection settings
        conn = root.find('Connection')
        if conn is not None:
            settings['auto_disconnect_enabled'] = conn.findtext('AutoDisconnectEnabled', 'true').lower() == 'true'
            settings['auto_disconnect_timeout'] = int(conn.findtext('AutoDisconnectTimeout', '30'))
            settings['auto_connect_startup'] = conn.findtext('AutoConnectOnStartup', 'false').lower() == 'true'
            settings['last_device'] = conn.findtext('LastUsedDevice')

        # Parse GUI settings
        gui = root.find('GUI')
        if gui is not None:
            settings['window_width'] = int(gui.findtext('WindowWidth', '800'))
            settings['window_height'] = int(gui.findtext('WindowHeight', '600'))
            settings['window_x'] = int(gui.findtext('WindowX', '100'))
            settings['window_y'] = int(gui.findtext('WindowY', '100'))
            settings['last_tab'] = int(gui.findtext('LastTab', '0'))

    except Exception as e:
        logging.getLogger('relay_control').warning(f"Failed to load settings: {e}. Using defaults.")

    return settings


def save_settings(settings: Dict[str, Any], custom_path: Optional[str] = None):
    """Save settings to XML"""
    settings_file = get_settings_path(custom_path)

    root = ET.Element('RelayControlSettings')

    # CLI settings
    cli = ET.SubElement(root, 'CLI')
    ET.SubElement(cli, 'QuietMode').text = str(settings['cli_quiet_mode']).lower()
    ET.SubElement(cli, 'VerboseMode').text = str(settings['cli_verbose_mode']).lower()
    ET.SubElement(cli, 'DefaultDuration').text = str(settings['default_duration'])
    ET.SubElement(cli, 'LogLevel').text = settings['log_level']
    ET.SubElement(cli, 'LogToFile').text = str(settings['log_to_file']).lower()
    if settings['log_file_path']:
        ET.SubElement(cli, 'LogFilePath').text = str(settings['log_file_path'])

    # Connection settings
    conn = ET.SubElement(root, 'Connection')
    ET.SubElement(conn, 'AutoDisconnectEnabled').text = str(settings['auto_disconnect_enabled']).lower()
    ET.SubElement(conn, 'AutoDisconnectTimeout').text = str(settings['auto_disconnect_timeout'])
    ET.SubElement(conn, 'AutoConnectOnStartup').text = str(settings['auto_connect_startup']).lower()
    if settings['last_device']:
        ET.SubElement(conn, 'LastUsedDevice').text = settings['last_device']

    # GUI settings
    gui = ET.SubElement(root, 'GUI')
    ET.SubElement(gui, 'WindowWidth').text = str(settings['window_width'])
    ET.SubElement(gui, 'WindowHeight').text = str(settings['window_height'])
    ET.SubElement(gui, 'WindowX').text = str(settings['window_x'])
    ET.SubElement(gui, 'WindowY').text = str(settings['window_y'])
    ET.SubElement(gui, 'LastTab').text = str(settings['last_tab'])

    # Write to file
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(settings_file, encoding='utf-8', xml_declaration=True)


# ============================================================================
# CORE FTDI FUNCTIONS
# ============================================================================

logger = logging.getLogger('relay_control')


def list_devices() -> List[Dict[str, Any]]:
    """
    Returns list of FTDI devices.
    Raises: NoDevicesFoundError if no devices found
    """
    try:
        num_devices = ftd.createDeviceInfoList()
        if num_devices == 0:
            raise NoDevicesFoundError()


        devices = []
        for i in range(num_devices):
            info = ftd.getDeviceInfoDetail(i)
            devices.append({
                'index': i,
                'description': info['description'].decode('utf-8') if isinstance(info['description'], bytes) else info[
                    'description'],
                'serial': info['serial'].decode('utf-8') if isinstance(info['serial'], bytes) else info['serial'],
                'type': info['type']
            })

        logger.info(f"Found {num_devices} FTDI device(s)")
        return devices

    except ftd.DeviceError as e:
        logger.error(f"FTDI driver error: {e}")
        #raise RelayControlException(f"FTDI driver error: {e}", ExitCode.FTDI_DRIVER_ERROR)
        return []
    except Exception as e:
        logger.error(f"Unexpected error listing devices: {e}")
        #raise RelayControlException(str(e), ExitCode.GENERAL_ERROR)
        return []


def connect_device(serial_number: str):
    """
    Opens device and initializes bit bang mode.
    Returns: device handle
    Raises: DeviceNotFoundError, ConnectionFailedError
    """
    try:
        logger.info(f"Attempting to connect to device: {serial_number}")

        handle = ftd.openEx(serial_number.encode('utf-8'))

        logger.debug("Setting baud rate to 9600")
        handle.setBaudRate(9600)

        logger.debug("Setting bit bang mode")
        handle.setBitMode(0xFF, 0x01)

        logger.info("Device connected successfully")
        return handle

    except ftd.DeviceError as e:
        logger.error(f"Failed to connect: {e}")
        error_str = str(e).lower()
        if "device not found" in error_str or "not open" in error_str:
            raise DeviceNotFoundError(serial_number)
        elif "access denied" in error_str or "claimed" in error_str:
            raise RelayControlException("Device in use by another application", ExitCode.DEVICE_IN_USE)
        else:
            raise ConnectionFailedError(str(e))
    except Exception as e:
        logger.error(f"Unexpected connection error: {e}")
        raise ConnectionFailedError(str(e))


def disconnect_device(handle):
    """Closes device cleanly"""
    try:
        if handle:
            handle.close()
            logger.info("Device disconnected")
    except Exception as e:
        logger.warning(f"Error during disconnect: {e}")


def get_relay_state(handle) -> int:
    """
    Reads current pin states.
    Returns: byte representing relay states
    """
    try:
        state = handle.getBitMode()
        logger.debug(f"Read relay state: 0x{state:02X}")
        return state
    except Exception as e:
        logger.error(f"Failed to read relay state: {e}")
        raise DeviceDisconnectedError()


def set_relay_state(handle, relay_mask: int):
    """
    Writes relay state to device.
    Raises: DeviceDisconnectedError, CommandExecutionFailedError
    """
    try:
        logger.debug(f"Writing relay state: 0x{relay_mask:02X}")
        handle.write(bytes([relay_mask]))
        logger.info("Relay state set successfully")

    except ftd.DeviceError as e:
        logger.error(f"Failed to write relay state: {e}")
        if "device not found" in str(e).lower():
            raise DeviceDisconnectedError()
        else:
            raise RelayControlException(f"Command execution failed: {e}", ExitCode.COMMAND_EXECUTION_FAILED)
    except Exception as e:
        logger.error(f"Unexpected error setting relay state: {e}")
        raise RelayControlException(str(e), ExitCode.COMMAND_EXECUTION_FAILED)


def relays_to_mask(relay_list: List[int]) -> int:
    """Convert relay numbers (1-4) to bit mask"""
    mask = 0
    for relay in relay_list:
        mask |= (1 << (relay - 1))
    return mask


def pulse_relays(handle, relay_list: List[int], duration: float):
    """Momentary pulse with auto-restore to previous state"""
    current_state = get_relay_state(handle)
    relay_mask = relays_to_mask(relay_list)

    # Turn on specified relays
    new_state = current_state | relay_mask
    set_relay_state(handle, new_state)

    # Wait
    logger.debug(f"Pulsing relays {relay_list} for {duration}s")
    time.sleep(duration)

    # Restore previous state
    set_relay_state(handle, current_state)
    logger.info(f"Relays {relay_list} pulsed and restored")


# ============================================================================
# CLI VALIDATION AND EXECUTION
# ============================================================================

def validate_relay_numbers(relay_list: Optional[List[int]], flag_name: str):
    """Validate relay numbers are within 1-4 range"""
    if not relay_list:
        return

    for relay in relay_list:
        if not isinstance(relay, int) or not (1 <= relay <= 4):
            raise InvalidRelayNumberError(f"{flag_name}: {relay}")

    logger.debug(f"Validated relay numbers for {flag_name}: {relay_list}")


def validate_arguments(args):
    """Validate all command-line arguments before execution"""
    validate_relay_numbers(args.state, '--state')
    validate_relay_numbers(args.on, '--on')
    validate_relay_numbers(args.off, '--off')
    validate_relay_numbers(args.toggle, '--toggle')
    validate_relay_numbers(args.momentary, '--momentary')

    # Check for conflicting flags
    if args.state and (args.on or args.off or args.toggle):
        raise ConflictingFlagsError("--state cannot be used with --on, --off, or --toggle")

    # Check for same relay in conflicting operations
    relay_ops = {}
    if args.on:
        for r in args.on:
            relay_ops[r] = 'on'
    if args.off:
        for r in args.off:
            if r in relay_ops:
                raise ConflictingFlagsError(f"Relay {r} specified in both --{relay_ops[r]} and --off")
            relay_ops[r] = 'off'

    # Validate duration only with momentary
    if args.duration is not None and not args.momentary:
        raise ConflictingFlagsError("--duration can only be used with --momentary")

    logger.debug("Argument validation passed")


def select_device_interactively(devices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prompt user to select from multiple devices"""
    print("\nMultiple FTDI devices found:")
    for idx, dev in enumerate(devices):
        print(f"  [{idx}] {dev['description']} (Serial: {dev['serial']})")
    print("  [X] Exit without connecting")

    while True:
        try:
            choice = input(f"\nSelect device [0-{len(devices) - 1}, X]: ").strip()

            if choice.upper() == 'X':
                print("Exiting without connecting.")
                return None

            idx = int(choice)
            if 0 <= idx < len(devices):
                return devices[idx]
            else:
                print(f"Error: Please enter a number between 0 and {len(devices) - 1}, or X to exit")

        except ValueError:
            print(f"Error: Invalid input. Enter a number (0-{len(devices) - 1}) or X to exit")
        except KeyboardInterrupt:
            print("\n\nCancelled by user.")
            return None
        except EOFError:
            print("\nError: No input available (running in non-interactive mode?)")
            print("Use --device-index or --device-serial when running in scripts")
            return None


def select_device(devices: List[Dict[str, Any]], args) -> Dict[str, Any]:
    """Select device based on args or prompt user"""
    if args.device_serial:
        for dev in devices:
            if dev['serial'] == args.device_serial:
                return dev
        raise DeviceNotFoundError(args.device_serial)

    elif args.device_index is not None:
        if not (0 <= args.device_index < len(devices)):
            raise RelayControlException(
                f"Device index {args.device_index} out of range (0-{len(devices) - 1})",
                ExitCode.DEVICE_NOT_FOUND
            )
        return devices[args.device_index]

    elif len(devices) == 1:
        return devices[0]

    else:
        device = select_device_interactively(devices)
        if device is None:
            sys.exit(ExitCode.SUCCESS)
        return device


def execute_relay_commands(handle, args, settings: Dict[str, Any], show_output: bool):
    """Execute relay commands based on parsed arguments"""

    # Get duration from args or settings
    duration = args.duration if args.duration is not None else settings['default_duration']

    # Execute --state (absolute state)
    if args.state:
        mask = relays_to_mask(args.state)
        set_relay_state(handle, mask)
        if show_output:
            print(f"Set absolute state: Relays {args.state} ON, others OFF")

    # Execute --on, --off, --toggle (modify existing state)
    else:
        current_state = get_relay_state(handle)

        if args.on:
            current_state |= relays_to_mask(args.on)
            if show_output:
                print(f"Turned ON: Relays {args.on}")

        if args.off:
            current_state &= ~relays_to_mask(args.off)
            if show_output:
                print(f"Turned OFF: Relays {args.off}")

        if args.toggle:
            current_state ^= relays_to_mask(args.toggle)
            if show_output:
                print(f"Toggled: Relays {args.toggle}")

        if args.on or args.off or args.toggle:
            set_relay_state(handle, current_state)

    # Execute --momentary (pulse)
    if args.momentary:
        pulse_relays(handle, args.momentary, duration)
        if show_output:
            print(f"Pulsed relays {args.momentary} for {duration}s")


# ============================================================================
# CLI MAIN
# ============================================================================

HELP_TEXT = """
SainSmart 4-Relay Control v1.0
A command-line and GUI tool for controlling FTDI-based relay boards.

USAGE:
    sainsmart_fdti_relay_control.exe                         Launch GUI
    sainsmart_fdti_relay_control.exe [OPTIONS] [COMMANDS]    CLI mode

DEVICE SELECTION:
    --list-devices                  List all available FTDI devices
    --device-index INDEX            Connect to device by index (0-based)
    --device-serial SERIAL          Connect to device by serial number

RELAY COMMANDS:
    --state RELAY [RELAY ...]       Set absolute state (specified ON, others OFF)
    --on RELAY [RELAY ...]          Turn on specified relays (leave others unchanged)
    --off RELAY [RELAY ...]         Turn off specified relays (leave others unchanged)
    --toggle RELAY [RELAY ...]      Toggle specified relays (leave others unchanged)
    --momentary RELAY [RELAY ...]   Pulse relays, then restore previous state
    --duration SECONDS              Duration for momentary pulse (default: 0.5s)

OUTPUT OPTIONS:
    --quiet, -q                     Suppress informational messages
    --verbose, -v                   Show detailed execution information
    --log-file PATH                 Write logs to specified file

CONFIGURATION:
    --config PATH                   Use custom settings XML file

EXAMPLES:
    sainsmart_fdti_relay_control.exe --list-devices
    sainsmart_fdti_relay_control.exe --on 1 2
    sainsmart_fdti_relay_control.exe --state 1 3
    sainsmart_fdti_relay_control.exe --momentary 2 --duration 2.0
    sainsmart_fdti_relay_control.exe --device-serial A1B2C3D4 --quiet --on 1

EXIT CODES:
    0=Success, 1=General error, 2=No devices, 3=Device not found,
    4=Connection failed, 5=Command failed, 6=Invalid args, 7=Disconnected,
    8=Permission denied, 9=Device in use, 10=Invalid relay, 11=Conflicting flags,
    12=FTDI driver error
"""


def main_cli(args):
    """CLI mode handler with comprehensive error handling"""

    settings = load_settings(args.config if hasattr(args, 'config') and args.config else None)

    log_level = 'ERROR' if args.quiet else ('DEBUG' if args.verbose else settings['log_level'])
    logger = setup_logging(
        log_level=log_level,
        log_to_file=args.log_file is not None or settings['log_to_file'],
        log_file_path=Path(args.log_file) if args.log_file else settings.get('log_file_path')
    )

    logger.info("=== CLI Mode Started ===")

    try:
        # Handle --list-devices
        if args.list_devices:
            devices = list_devices()
            print("Available FTDI devices:")
            for idx, dev in enumerate(devices):
                print(f"  [{idx}] {dev['description']} (Serial: {dev['serial']})")
            sys.exit(ExitCode.SUCCESS)

        # Validate arguments
        validate_arguments(args)

        # Device selection
        devices = list_devices()
        device = select_device(devices, args)

        if not args.quiet:
            print(f"Connecting to: {device['description']} (Serial: {device['serial']})")

        # Connect
        handle = connect_device(device['serial'])

        try:
            # Execute commands
            execute_relay_commands(handle, args, settings, not args.quiet)

            if not args.quiet:
                print("Command executed successfully.")

            logger.info("=== CLI Mode Completed Successfully ===")
            sys.exit(ExitCode.SUCCESS)

        finally:
            disconnect_device(handle)

    except RelayControlException as e:
        logger.error(f"Error: {e.message}")
        print(f"Error: {e.message}", file=sys.stderr)
        sys.exit(e.exit_code)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        print("\nOperation cancelled by user.")
        sys.exit(ExitCode.SUCCESS)

    except Exception as e:
        logger.exception("Unexpected error occurred")
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(ExitCode.GENERAL_ERROR)


# ============================================================================
# GUI (wxPython)
# ============================================================================

if wx:
    class RelayControlFrame(wx.Frame):
        def __init__(self):
            super().__init__(None, title="SainSmart 4-Relay Control", size=(800, 600))

            self.settings = load_settings()
            self.handle = None
            self.is_connected = False
            self.relay_states = [False, False, False, False]

            self.init_ui()
            self.Centre()

            # Setup logging for GUI
            setup_logging(log_level=self.settings['log_level'])

        def init_ui(self):
            """Initialize the user interface"""
            panel = wx.Panel(self)
            main_sizer = wx.BoxSizer(wx.VERTICAL)

            # Connection panel
            conn_panel = self.create_connection_panel(panel)
            main_sizer.Add(conn_panel, 0, wx.EXPAND | wx.ALL, 5)

            # Notebook (tabs)
            notebook = wx.Notebook(panel)

            # Basic tab
            basic_tab = self.create_basic_tab(notebook)
            notebook.AddPage(basic_tab, "Basic Control")

            # Advanced tab placeholder
            advanced_tab = wx.Panel(notebook)
            notebook.AddPage(advanced_tab, "Advanced")

            # Presets tab placeholder
            presets_tab = wx.Panel(notebook)
            notebook.AddPage(presets_tab, "Presets")

            main_sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)

            # CLI command display
            cli_panel = self.create_cli_panel(panel)
            main_sizer.Add(cli_panel, 0, wx.EXPAND | wx.ALL, 5)

            panel.SetSizer(main_sizer)

            # Status bar
            self.CreateStatusBar()
            self.SetStatusText("Ready | Not connected")

            # Initially disable relay controls
            self.update_control_states()

        def create_connection_panel(self, parent):
            """Create device connection panel"""
            panel = wx.Panel(parent)
            sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Device label and dropdown
            device_label = wx.StaticText(panel, label="Device:")
            sizer.Add(device_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)

            self.device_choice = wx.Choice(panel, choices=["No devices found"])
            sizer.Add(self.device_choice, 1, wx.ALL, 5)

            # Refresh button
            self.refresh_btn = wx.Button(panel, label="Refresh")
            self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
            sizer.Add(self.refresh_btn, 0, wx.ALL, 5)

            # Status indicator
            self.status_text = wx.StaticText(panel, label="○ Disconnected")
            sizer.Add(self.status_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)

            # Connect/Disconnect button
            self.connect_btn = wx.Button(panel, label="Connect")
            self.connect_btn.Bind(wx.EVT_BUTTON, self.on_connect)
            sizer.Add(self.connect_btn, 0, wx.ALL, 5)

            panel.SetSizer(sizer)

            # Initial device scan
            self.refresh_devices()

            return panel

        def create_basic_tab(self, parent):
            """Create basic relay control tab"""
            panel = wx.Panel(parent)
            sizer = wx.BoxSizer(wx.VERTICAL)

            # Title
            title = wx.StaticText(panel, label="Individual Relay Control")
            title_font = title.GetFont()
            title_font.PointSize += 2
            title_font = title_font.Bold()
            title.SetFont(title_font)
            sizer.Add(title, 0, wx.ALL, 10)

            # Create relay controls
            self.relay_controls = []
            for i in range(4):
                relay_panel = self.create_relay_control(panel, i + 1)
                sizer.Add(relay_panel, 0, wx.EXPAND | wx.ALL, 5)

            # All on/off buttons
            all_sizer = wx.BoxSizer(wx.HORIZONTAL)
            all_sizer.AddStretchSpacer()

            self.all_on_btn = wx.Button(panel, label="ALL ON")
            self.all_on_btn.Bind(wx.EVT_BUTTON, self.on_all_on)
            all_sizer.Add(self.all_on_btn, 0, wx.ALL, 5)

            self.all_off_btn = wx.Button(panel, label="ALL OFF")
            self.all_off_btn.Bind(wx.EVT_BUTTON, self.on_all_off)
            all_sizer.Add(self.all_off_btn, 0, wx.ALL, 5)

            sizer.Add(all_sizer, 0, wx.EXPAND | wx.ALL, 10)

            panel.SetSizer(sizer)
            return panel

        def create_relay_control(self, parent, relay_num):
            """Create control panel for a single relay"""
            panel = wx.Panel(parent)
            sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Relay label
            label = wx.StaticText(panel, label=f"Relay {relay_num}:")
            sizer.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)

            # ON button
            on_btn = wx.Button(panel, label="ON", size=(60, -1))
            on_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_relay_on(relay_num))
            sizer.Add(on_btn, 0, wx.ALL, 2)

            # OFF button
            off_btn = wx.Button(panel, label="OFF", size=(60, -1))
            off_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_relay_off(relay_num))
            sizer.Add(off_btn, 0, wx.ALL, 2)

            # TOGGLE button
            toggle_btn = wx.Button(panel, label="TOGGLE", size=(80, -1))
            toggle_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_relay_toggle(relay_num))
            sizer.Add(toggle_btn, 0, wx.ALL, 2)

            # Status indicator
            status = wx.StaticText(panel, label="○")
            status.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            sizer.Add(status, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)

            panel.SetSizer(sizer)

            # Store controls for later reference
            self.relay_controls.append({
                'panel': panel,
                'on_btn': on_btn,
                'off_btn': off_btn,
                'toggle_btn': toggle_btn,
                'status': status
            })

            return panel

        def create_cli_panel(self, parent):
            """Create CLI command display panel"""
            panel = wx.Panel(parent)
            sizer = wx.BoxSizer(wx.VERTICAL)

            label = wx.StaticText(panel, label="CLI Command:")
            sizer.Add(label, 0, wx.ALL, 5)

            self.cli_text = wx.TextCtrl(panel, style=wx.TE_READONLY, size=(-1, 60))
            self.cli_text.SetValue("# Connect to device first")
            sizer.Add(self.cli_text, 0, wx.EXPAND | wx.ALL, 5)

            btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
            btn_sizer.AddStretchSpacer()

            copy_btn = wx.Button(panel, label="Copy")
            copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_cli)
            btn_sizer.Add(copy_btn, 0, wx.ALL, 2)

            sizer.Add(btn_sizer, 0, wx.EXPAND)

            panel.SetSizer(sizer)
            return panel

        def refresh_devices(self):
            """Scan for FTDI devices and populate dropdown"""
            try:
                devices = list_devices()

                device_labels = []
                for dev in devices:
                    device_labels.append(f"{dev['description']} (Serial: {dev['serial']})")

                self.device_choice.SetItems(device_labels)
                self.devices = devices

                # Auto-select last used device or first device
                if self.settings['last_device']:
                    for idx, dev in enumerate(devices):
                        if dev['serial'] == self.settings['last_device']:
                            self.device_choice.SetSelection(idx)
                            break
                    else:
                        self.device_choice.SetSelection(0)
                else:
                    self.device_choice.SetSelection(0)

                self.connect_btn.Enable(True)

            except NoDevicesFoundError:
                self.device_choice.SetItems(["No devices found"])
                self.device_choice.SetSelection(0)
                self.devices = []
                self.connect_btn.Enable(False)

        def on_refresh(self, event):
            """Handle refresh button click"""
            if self.is_connected:
                wx.MessageBox("Please disconnect before refreshing devices.",
                              "Device Connected", wx.OK | wx.ICON_WARNING)
                return

            self.refresh_devices()
            self.SetStatusText("Device list refreshed")

        def on_connect(self, event):
            """Handle connect/disconnect button click"""
            if self.is_connected:
                self.disconnect()
            else:
                self.connect()

        def connect(self):
            """Connect to selected device"""
            if not self.devices:
                wx.MessageBox("No devices available to connect.",
                              "No Devices", wx.OK | wx.ICON_ERROR)
                return

            selected_idx = self.device_choice.GetSelection()
            if selected_idx < 0:
                return

            device = self.devices[selected_idx]

            try:
                self.handle = connect_device(device['serial'])
                self.is_connected = True

                # Read initial state
                current_state = get_relay_state(self.handle)
                for i in range(4):
                    self.relay_states[i] = bool(current_state & (1 << i))
                self.update_status_leds()

                # Update UI
                self.status_text.SetLabel("● Connected")
                self.status_text.SetForegroundColour(wx.Colour(0, 200, 0))
                self.connect_btn.SetLabel("Disconnect")
                self.device_choice.Enable(False)
                self.refresh_btn.Enable(False)

                self.update_control_states()

                # Save last used device
                self.settings['last_device'] = device['serial']
                save_settings(self.settings)

                self.SetStatusText(f"Connected to {device['description']} on COM port")

            except RelayControlException as e:
                wx.MessageBox(f"Failed to connect:\n{e.message}",
                              "Connection Error", wx.OK | wx.ICON_ERROR)

        def disconnect(self):
            """Disconnect from device"""
            disconnect_device(self.handle)
            self.handle = None
            self.is_connected = False

            # Update UI
            self.status_text.SetLabel("○ Disconnected")
            self.status_text.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
            self.connect_btn.SetLabel("Connect")
            self.device_choice.Enable(True)
            self.refresh_btn.Enable(True)

            self.update_control_states()

            self.SetStatusText("Disconnected")
            self.cli_text.SetValue("# Connect to device first")

        def update_control_states(self):
            """Enable/disable controls based on connection state"""
            for controls in self.relay_controls:
                controls['on_btn'].Enable(self.is_connected)
                controls['off_btn'].Enable(self.is_connected)
                controls['toggle_btn'].Enable(self.is_connected)

            if hasattr(self, 'all_on_btn'):
                self.all_on_btn.Enable(self.is_connected)
                self.all_off_btn.Enable(self.is_connected)

        def update_status_leds(self):
            """Update LED indicators based on relay states"""
            for i, controls in enumerate(self.relay_controls):
                if self.relay_states[i]:
                    controls['status'].SetLabel("●")
                    controls['status'].SetForegroundColour(wx.Colour(0, 200, 0))
                else:
                    controls['status'].SetLabel("○")
                    controls['status'].SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))

        def on_relay_on(self, relay_num):
            """Turn on specific relay"""
            if not self.is_connected:
                return

            try:
                current_state = get_relay_state(self.handle)
                new_state = current_state | (1 << (relay_num - 1))
                set_relay_state(self.handle, new_state)

                self.relay_states[relay_num - 1] = True
                self.update_status_leds()

                device = self.devices[self.device_choice.GetSelection()]
                self.cli_text.SetValue(
                    f"sainsmart_fdti_relay_control.exe --device-serial {device['serial']} --on {relay_num}"
                )

            except RelayControlException as e:
                wx.MessageBox(f"Command failed:\n{e.message}",
                              "Error", wx.OK | wx.ICON_ERROR)
                if isinstance(e, DeviceDisconnectedError):
                    self.disconnect()

        def on_relay_off(self, relay_num):
            """Turn off specific relay"""
            if not self.is_connected:
                return

            try:
                current_state = get_relay_state(self.handle)
                new_state = current_state & ~(1 << (relay_num - 1))
                set_relay_state(self.handle, new_state)

                self.relay_states[relay_num - 1] = False
                self.update_status_leds()

                device = self.devices[self.device_choice.GetSelection()]
                self.cli_text.SetValue(
                    f"sainsmart_fdti_relay_control.exe --device-serial {device['serial']} --off {relay_num}"
                )

            except RelayControlException as e:
                wx.MessageBox(f"Command failed:\n{e.message}",
                              "Error", wx.OK | wx.ICON_ERROR)
                if isinstance(e, DeviceDisconnectedError):
                    self.disconnect()

        def on_relay_toggle(self, relay_num):
            """Toggle specific relay"""
            if not self.is_connected:
                return

            try:
                current_state = get_relay_state(self.handle)
                new_state = current_state ^ (1 << (relay_num - 1))
                set_relay_state(self.handle, new_state)

                self.relay_states[relay_num - 1] = not self.relay_states[relay_num - 1]
                self.update_status_leds()

                device = self.devices[self.device_choice.GetSelection()]
                self.cli_text.SetValue(
                    f"sainsmart_fdti_relay_control.exe --device-serial {device['serial']} --toggle {relay_num}"
                )

            except RelayControlException as e:
                wx.MessageBox(f"Command failed:\n{e.message}",
                              "Error", wx.OK | wx.ICON_ERROR)
                if isinstance(e, DeviceDisconnectedError):
                    self.disconnect()

        def on_all_on(self, event):
            """Turn all relays on"""
            if not self.is_connected:
                return

            try:
                set_relay_state(self.handle, 0x0F)
                self.relay_states = [True, True, True, True]
                self.update_status_leds()

                device = self.devices[self.device_choice.GetSelection()]
                self.cli_text.SetValue(
                    f"sainsmart_fdti_relay_control.exe --device-serial {device['serial']} --state 1 2 3 4"
                )

            except RelayControlException as e:
                wx.MessageBox(f"Command failed:\n{e.message}",
                              "Error", wx.OK | wx.ICON_ERROR)
                if isinstance(e, DeviceDisconnectedError):
                    self.disconnect()

        def on_all_off(self, event):
            """Turn all relays off"""
            if not self.is_connected:
                return

            try:
                set_relay_state(self.handle, 0x00)
                self.relay_states = [False, False, False, False]
                self.update_status_leds()

                device = self.devices[self.device_choice.GetSelection()]
                self.cli_text.SetValue(
                    f"sainsmart_fdti_relay_control.exe --device-serial {device['serial']} --state"
                )

            except RelayControlException as e:
                wx.MessageBox(f"Command failed:\n{e.message}",
                              "Error", wx.OK | wx.ICON_ERROR)
                if isinstance(e, DeviceDisconnectedError):
                    self.disconnect()

        def on_copy_cli(self, event):
            """Copy CLI command to clipboard"""
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(self.cli_text.GetValue()))
                wx.TheClipboard.Close()
                self.SetStatusText("CLI command copied to clipboard")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point - determines CLI or GUI mode"""
    if len(sys.argv) > 1:
        # CLI mode
        parser = argparse.ArgumentParser(
            description="SainSmart 4-Relay Control",
            add_help=False
        )

        # Help
        parser.add_argument('--help', '-h', action='store_true',
                            help='Show help message')

        # Device selection
        parser.add_argument('--list-devices', action='store_true',
                            help='List available FTDI devices')
        parser.add_argument('--device-index', type=int,
                            help='Connect to device by index')
        parser.add_argument('--device-serial', type=str,
                            help='Connect to device by serial number')

        # Relay commands
        parser.add_argument('-s','--state', nargs='+', type=int, metavar='RELAY',
                            help='Set absolute state')
        parser.add_argument('--on', nargs='+', type=int, metavar='RELAY',
                            help='Turn on specified relays')
        parser.add_argument('--off', nargs='+', type=int, metavar='RELAY',
                            help='Turn off specified relays')
        parser.add_argument('-t' ,'--toggle', nargs='+', type=int, metavar='RELAY',
                            help='Toggle specified relays')
        parser.add_argument('-m', '--momentary', nargs='+', type=int, metavar='RELAY',
                            help='Pulse specified relays')
        parser.add_argument('-d', '--duration', type=float,
                            help='Duration for momentary pulse')

        # Output options
        parser.add_argument('--quiet', '-q', action='store_true',
                            help='Suppress informational messages')
        parser.add_argument('--verbose', '-v', action='store_true',
                            help='Show detailed execution info')
        parser.add_argument('--log-file', type=str,
                            help='Write logs to file')

        # Configuration
        parser.add_argument('--config', type=str,
                            help='Use custom settings XML file')

        args = parser.parse_args()

        if args.help:
            print(HELP_TEXT)
            sys.exit(0)

        main_cli(args)

    else:
        # GUI mode
        if wx is None:
            print("Error: wxPython not installed. Cannot launch GUI.")
            print("Install with: pip install wxPython")
            print("Or use CLI mode: sainsmart_fdti_relay_control.exe --help")
            sys.exit(1)

        app = wx.App()
        frame = RelayControlFrame()
        frame.Show()
        app.MainLoop()


if __name__ == "__main__":
    main()