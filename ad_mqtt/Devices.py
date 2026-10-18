
def normalize_serial(serial):
    """Normalize an RF serial to a string with no leading zeros.

    The panel reports serials zero-padded (e.g. '0789186') while users
    typically write them as ints in devices.py (789186).  Normalize both
    so lookups match.
    """
    return str(serial).lstrip('0') or '0'


def init_devices(devices):
    zones, rf_devices = {}, {}

    for d in devices:
        if not isinstance(d, Rf):
            zones[d.zone] = d
        else:
            rf_devices[d.id] = d
            for loop in d.loops:
                if loop is not None:
                    zones[loop.zone] = loop

    return zones, rf_devices


class Zone:
    def __init__(self, zone, entity, label, device_class=None):
        self.zone = zone   # int zone number
        self.entity = entity
        self.label = label
        self.faulted = None  # None=Unknown, True=faulted, False=clear
        self.has_battery = False
        self.unique_id = None
        self.device_class = device_class if device_class else \
                            guess_class((entity, label))


class Rf:
    def __init__(self, id, loops):
        assert len(loops) <= 4
        self.id = normalize_serial(id)
        self.loops = [None] * 4
        for i, l in enumerate(loops):
            self.loops[i] = l
            if l:
                l.has_battery = True


class RfZone (Rf):
    """Single-loop RF zone.

    loop is the 1-based RF loop number the contact is on (default 1).
    Many sensors report different functions on different loops; check
    the raw RFX values in the RF log (rf_log.jsonl) to find the right one.
    """
    def __init__(self, id, zone, entity, label, loop=1):
        assert 1 <= loop <= 4
        zones = [None] * 4
        zones[loop - 1] = Zone(zone, entity, label)
        super().__init__(id, zones)


class_keywords = {
    # Map of keyword to HASS device class
    "fire" : "smoke",
    "door" : "door",
    "window" : "window",
    }


def guess_class(labels):
    for lbl in labels:
        s = lbl.lower()
        for k, v in class_keywords.items():
            if k in s:
                return v

    return None
