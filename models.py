from dataclasses import dataclass, field
from typing import Optional, List


ALL_EVENT_TYPES = [
    'Sequence', 'Random', 'Touch-Screen', 'Touch-Area', 'Button',
    'Wait', 'Insert-Credit', 'Clear-Jackpot', 'Door', 'Switch',
    'Random-Credit', 'Swipe-Screen', 'Condition', 'Scheduled', 'Simultaneous',
    'meter-list', 'output',
]

CONTAINER_TYPES = {'Sequence', 'Random', 'Simultaneous', 'Condition', 'Scheduled'}
TOUCH_TYPES = {'Touch-Screen', 'Touch-Area', 'Swipe-Screen'}
LEAF_TYPES = {'Button', 'Wait', 'Insert-Credit', 'Clear-Jackpot', 'Door',
              'Switch', 'Random-Credit', 'output'}

ALL_METERS: List[str] = [
    'Video-Used-Memory', 'Video-Free-Memory', 'Video-Total-Memory',
    'Used-Memory', 'Free-Memory', 'Total-Memory', 'File-Buffer-Cache',
    'Page-Cache', 'Real-Free-Memory', 'CMR', 'Max-CMR',
    'Sys-CPU', 'User-CPU', 'Idle-CPU',
    'Games-Played', 'Turnover', 'Total-Win', 'Credit',
    'Bet', 'Last-Win', 'Hopper-Pay', 'Jackpot',
]

BUTTON_KEYS: List[str] = ['Play', 'Bet-5', 'Bet-1', 'Play-Line-3', 'Play-Line-1']

TAG_COLORS = {
    'container': '#1565C0',
    'touch':     '#2E7D32',
    'control':   '#E65100',
    'utility':   '#AD1457',
    'special':   '#546E7A',
    'condition': '#6A1B9A',
}

TYPE_TAG_MAP = {
    'Sequence': 'container', 'Random': 'container',
    'Simultaneous': 'container', 'Scheduled': 'container',
    'Touch-Screen': 'touch', 'Touch-Area': 'touch', 'Swipe-Screen': 'touch',
    'Button': 'control', 'Wait': 'control',
    'Insert-Credit': 'utility', 'Clear-Jackpot': 'utility',
    'Door': 'utility', 'Switch': 'utility', 'Random-Credit': 'utility',
    'Condition': 'condition',
    'meter-list': 'special', 'output': 'special',
}

# Attribute emission order per event type (for XML output)
ATTR_ORDER = {
    'Button':        ['key', 'value'],
    'Wait':          ['state', 'timeout', 'units'],
    'Insert-Credit': ['value', 'when_below'],
    'Door':          ['door', 'open'],
    'Switch':        ['switch', 'off'],
    'Random-Credit': ['range'],
    'Scheduled':     ['timeout', 'units'],
}

# Attrs whose Python key uses underscore but XML uses hyphen
ATTR_KEY_MAP = {
    'when_below': 'when-below',
}

# Default attribute values for new nodes
DEFAULT_ATTRS = {
    'meter-list': {
        'timeout': '15',
        'units': 'Seconds',
        'output_filename': 'robotlogs/eventsfile.txt',
        'output_append': 'False',
        'meters': None,  # filled in __post_init__ of RobotNode.new()
    },
    'output': {
        'filename': 'robotlogs/eventsfile.txt',
        'append': 'False',
    },
    'Button':        {'key': 'Play', 'value': ''},
    'Wait':          {'timeout': '3', 'units': 'Seconds', 'state': ''},
    'Insert-Credit': {'value': '2048', 'when_below': '512'},
    'Door':          {'door': 'Logic', 'open': 'True'},
    'Switch':        {'switch': '2', 'off': ''},
    'Random-Credit': {'range': '100'},
    'Scheduled':     {'timeout': '60', 'units': 'Seconds'},
}


@dataclass
class RobotNode:
    node_type: str
    id: str = ""
    weight: Optional[int] = None
    comment: str = ""
    attrs: dict = field(default_factory=dict)
    points: list = field(default_factory=list)   # list of [x, y] pairs
    children: list = field(default_factory=list)  # list of RobotNode

    @classmethod
    def new(cls, node_type: str) -> 'RobotNode':
        import copy
        node = cls(node_type=node_type)
        defaults = DEFAULT_ATTRS.get(node_type, {})
        node.attrs = copy.deepcopy(defaults)
        # meter-list: fill meters list after deepcopy
        if node_type == 'meter-list' and node.attrs.get('meters') is None:
            node.attrs['meters'] = list(ALL_METERS)
        # Initialize default points
        if node_type == 'Touch-Screen':
            node.points = [[0, 0]]
        elif node_type in ('Touch-Area', 'Swipe-Screen'):
            node.points = [[0, 0], [100, 100]]
        return node


def classify_tag(node_type: str) -> str:
    return TYPE_TAG_MAP.get(node_type, 'special')


def display_label(node: RobotNode):
    """Returns (tree_text, detail_text) for the treeview columns."""
    t = node.node_type
    d = node.id or ''

    if t == 'Touch-Screen':
        if node.points:
            d = f"({node.points[0][0]}, {node.points[0][1]})"
        if node.weight is not None:
            d += f"  w={node.weight}"
    elif t == 'Touch-Area':
        if len(node.points) >= 2:
            d = (f"({node.points[0][0]},{node.points[0][1]}) "
                 f"→ ({node.points[1][0]},{node.points[1][1]})")
        if node.weight is not None:
            d += f"  w={node.weight}"
    elif t == 'Swipe-Screen':
        if len(node.points) >= 2:
            d = (f"({node.points[0][0]},{node.points[0][1]}) "
                 f"→ ({node.points[1][0]},{node.points[1][1]})")
    elif t == 'Button':
        key = node.attrs.get('key', '')
        d = key
        if node.weight is not None:
            d += f"  w={node.weight}"
    elif t == 'Wait':
        state = node.attrs.get('state', '')
        timeout = node.attrs.get('timeout', '')
        units = node.attrs.get('units', '')
        short = {'Seconds': 's', 'Minutes': 'm', '': 'ms'}.get(units, units)
        d = f"{state}  {timeout}{short}" if state else f"{timeout}{short}"
    elif t == 'Insert-Credit':
        v = node.attrs.get('value', '')
        wb = node.attrs.get('when_below', '')
        d = f"value={v}  <{wb}"
    elif t == 'meter-list':
        timeout = node.attrs.get('timeout', '')
        units_short = {'Seconds': 's', 'Minutes': 'm'}.get(node.attrs.get('units', ''), 's')
        n_meters = len(node.attrs.get('meters') or [])
        d = f"{timeout}{units_short}  {n_meters} meters"
    elif t == 'output':
        d = node.attrs.get('filename', '')
    elif t == 'Random':
        total_w = sum((c.weight or 0) for c in node.children)
        d = f"{len(node.children)} options  Σw={total_w}"
    elif t == 'Sequence':
        d = f"{len(node.children)} steps"
        if node.weight is not None:
            d += f"  w={node.weight}"
    elif t == 'Clear-Jackpot':
        d = ''
    elif t == 'Random-Credit':
        d = f"range={node.attrs.get('range', '')}"
    elif t in ('Door', 'Switch'):
        d = '  '.join(f"{k}={v}" for k, v in node.attrs.items() if v)

    return t, d
