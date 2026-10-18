import logging
import logging.handlers
import alarmdecoder as AD
import insteon_mqtt as IM
from .Bridge import Bridge
from .Client import Client
from . import Devices
from .Discovery import Discovery
from .RfLog import RfLog


def setup_logging(log_cfg):
    fmt = '%(asctime)s.%(msecs)03d %(levelname)s %(module)s: %(message)s'
    datefmt = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter(fmt, datefmt)

    if log_cfg.screen:
        screen_handler = logging.StreamHandler()
        screen_handler.setFormatter(formatter)

    if log_cfg.file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_cfg.file, maxBytes=log_cfg.size_kb * 1000,
            backupCount=log_cfg.backup_count)
        file_handler.setFormatter(formatter)

    for name in log_cfg.modules:
        log = logging.getLogger(name)
        log.setLevel(log_cfg.level)
        if log_cfg.screen:
            log.addHandler(screen_handler)
        if log_cfg.file:
            log.addHandler(file_handler)


def run(cfg, alarm_code, devices, restore_all=False):
    setup_logging(cfg.log)

    log = logging.getLogger(__name__)

    try:
        zones, rf_devices = Devices.init_devices(devices)

        # Alarm decoder network device.
        ad_client = Client(cfg.alarm.host, cfg.alarm.port)
        decoder = AD.AlarmDecoder(ad_client)
        # Required to register the decoder's event callbacks.
        decoder._wire_events()  # pylint: disable=protected-access

        mqtt_client = IM.network.Mqtt(id="ad-mqtt")
        # IM uses dict: cfg['a'] not cfg.a
        mqtt_client.load_config(cfg.mqtt.__dict__)

        rf_log = None
        if cfg.alarm.rf_log_file:
            rf_log = RfLog(cfg.alarm.rf_log_file,
                           max_bytes=cfg.alarm.rf_log_max_mb * 1024 * 1024)

        bridge = Bridge(mqtt_client, decoder, alarm_code, zones, rf_devices,
                        rf_log=rf_log)
        # Discovery subscribes to the connected signal, which only holds a
        # weak reference, so keep a strong reference for the process lifetime.
        discovery = Discovery(mqtt_client, bridge, zones)
        log.debug("Queued %d discovery messages", len(discovery.messages))

        loop = IM.network.poll.Manager()
        loop.add(ad_client, connected=False)
        loop.add(mqtt_client, connected=False)

        if cfg.alarm.restore_on_startup:
            bridge.reset_all_zones()

        while loop.active():
            try:
                loop.select()
            except Exception:  # pylint: disable=broad-except
                # A single bad message or transient handler error should not
                # bring down the bridge.  Log it and keep the loop running.
                # KeyboardInterrupt/SystemExit are BaseExceptions, so they
                # still propagate and can stop the process.
                log.exception("Unexpected exception in poll loop")
    except:
        log.exception("Unexpected exception")
        raise
