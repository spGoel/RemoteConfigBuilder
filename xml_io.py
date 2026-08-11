import xml.etree.ElementTree as ET
from typing import List, Optional

from models import (
    RobotNode, ALL_METERS, ALL_EVENT_TYPES,
    CONTAINER_TYPES, TOUCH_TYPES, LEAF_TYPES,
    ATTR_ORDER, ATTR_KEY_MAP,
)

TAB = "\t"


# ═══════════════════════════════════════
#  XML Generation
# ═══════════════════════════════════════

def generate_xml(root_node: RobotNode) -> str:
    return _node_to_str(root_node, 0)


def _node_to_str(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    lines = []

    if node.comment:
        lines.append(f"{pad}<!-- {node.comment} -->")

    if node.node_type == 'meter-list':
        lines.append(_gen_meter_list(node, indent))
    elif node.node_type == 'output':
        fname = node.attrs.get('filename', 'robotlogs/eventsfile.txt')
        append = node.attrs.get('append', 'False')
        lines.append(f'{pad}<output filename="{fname}" append="{append}"/>')
    elif node.node_type == 'Clear-Jackpot':
        lines.append(f'{pad}<event type="Clear-Jackpot"/>')
    elif node.node_type in TOUCH_TYPES:
        lines.append(_gen_touch_event(node, indent))
    elif node.node_type in LEAF_TYPES:
        lines.append(_gen_leaf_event(node, indent))
    else:
        lines.append(_gen_container_event(node, indent))

    return '\n'.join(lines)


def _build_event_attrs(node: RobotNode) -> str:
    parts = [f'type="{node.node_type}"']
    if node.id:
        parts.append(f'id="{node.id}"')
    if node.weight is not None:
        parts.append(f'weight="{node.weight}"')
    for key in ATTR_ORDER.get(node.node_type, []):
        val = node.attrs.get(key)
        if val is not None and val != '':
            xml_key = ATTR_KEY_MAP.get(key, key)
            parts.append(f'{xml_key}="{val}"')
    return ' '.join(parts)


def _gen_leaf_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    return f'{pad}<event {_build_event_attrs(node)}/>'


def _gen_touch_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    lines = [f'{pad}<event {_build_event_attrs(node)}>']
    for pt in node.points:
        lines.append(f'{pad}{TAB}<point x="{pt[0]}" y="{pt[1]}"/>')
    lines.append(f'{pad}</event>')
    return '\n'.join(lines)


def _gen_container_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    lines = [f'{pad}<event {_build_event_attrs(node)}>']
    for child in node.children:
        lines.append(_node_to_str(child, indent + 1))
    lines.append(f'{pad}</event>')
    return '\n'.join(lines)


def _gen_meter_list(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    timeout = node.attrs.get('timeout', '15')
    units = node.attrs.get('units', 'Seconds')
    fname = node.attrs.get('output_filename', 'robotlogs/eventsfile.txt')
    append = node.attrs.get('output_append', 'False')
    meters = node.attrs.get('meters') or ALL_METERS

    lines = [
        f'{pad}<meter-list timeout="{timeout}" units="{units}">',
        f'{pad}{TAB}<output filename="{fname}" append="{append}"/>',
    ]
    for m in meters:
        lines.append(f'{pad}{TAB}<meter>{m}</meter>')
    lines.append(f'{pad}</meter-list>')
    return '\n'.join(lines)


# ═══════════════════════════════════════
#  XML Parsing
# ═══════════════════════════════════════

def parse_xml_file(filepath: str) -> RobotNode:
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(filepath, parser)
    except TypeError:
        # Python < 3.8 fallback (no insert_comments)
        tree = ET.parse(filepath)
    root = tree.getroot()
    node = _parse_element(root)
    if node is None:
        raise ValueError("Root element could not be parsed.")
    return node


def _parse_element(elem) -> Optional[RobotNode]:
    tag = elem.tag
    if callable(tag):   # comment pseudo-element
        return None
    if tag == 'meter-list':
        return _parse_meter_list(elem)
    if tag == 'output':
        node = RobotNode(node_type='output')
        node.attrs['filename'] = elem.get('filename', 'robotlogs/eventsfile.txt')
        node.attrs['append'] = elem.get('append', 'False')
        return node
    if tag == 'event':
        return _parse_event(elem)
    return None


def _parse_meter_list(elem) -> RobotNode:
    node = RobotNode(node_type='meter-list')
    node.attrs['timeout'] = elem.get('timeout', '15')
    node.attrs['units'] = elem.get('units', 'Seconds')
    node.attrs['output_filename'] = 'robotlogs/eventsfile.txt'
    node.attrs['output_append'] = 'False'
    node.attrs['meters'] = []
    for child in elem:
        if callable(child.tag):
            continue
        if child.tag == 'output':
            node.attrs['output_filename'] = child.get('filename', 'robotlogs/eventsfile.txt')
            node.attrs['output_append'] = child.get('append', 'False')
        elif child.tag == 'meter' and child.text:
            node.attrs['meters'].append(child.text.strip())
    return node


def _parse_event(elem) -> RobotNode:
    event_type = elem.get('type', 'Sequence')
    node = RobotNode(node_type=event_type)
    node.id = elem.get('id', '')
    raw_w = elem.get('weight')
    node.weight = int(raw_w) if raw_w is not None else None

    # Type-specific attributes
    if event_type == 'Button':
        node.attrs['key'] = elem.get('key', 'Play')
        node.attrs['value'] = elem.get('value', '') or ''
    elif event_type == 'Wait':
        node.attrs['timeout'] = elem.get('timeout', '3')
        node.attrs['units'] = elem.get('units', 'Seconds')
        node.attrs['state'] = elem.get('state', '')
    elif event_type == 'Insert-Credit':
        node.attrs['value'] = elem.get('value', '2048')
        node.attrs['when_below'] = elem.get('when-below', '512')
    elif event_type == 'Door':
        node.attrs['door'] = elem.get('door', 'Logic')
        node.attrs['open'] = elem.get('open', 'True')
    elif event_type == 'Switch':
        node.attrs['switch'] = elem.get('switch', '2')
        node.attrs['off'] = elem.get('off', '')
    elif event_type == 'Random-Credit':
        node.attrs['range'] = elem.get('range', '100')
    elif event_type == 'Scheduled':
        node.attrs['timeout'] = elem.get('timeout', '60')
        node.attrs['units'] = elem.get('units', 'Seconds')

    # Parse children and points
    pending_comment = ""
    for child in elem:
        if callable(child.tag):
            pending_comment = (child.text or "").strip()
            continue
        if child.tag == 'point':
            node.points.append([int(child.get('x', 0)), int(child.get('y', 0))])
        else:
            child_node = _parse_element(child)
            if child_node is not None:
                child_node.comment = pending_comment
                pending_comment = ""
                node.children.append(child_node)

    return node


# ═══════════════════════════════════════
#  Validation
# ═══════════════════════════════════════

def validate_tree(root: RobotNode) -> List[str]:
    errors: List[str] = []
    _validate_node(root, errors, "root")
    return errors


def _validate_node(node: RobotNode, errors: List[str], path: str):
    t = node.node_type
    if t == 'Touch-Screen' and len(node.points) != 1:
        errors.append(f"{path}: Touch-Screen needs exactly 1 point (has {len(node.points)})")
    elif t in ('Touch-Area', 'Swipe-Screen') and len(node.points) != 2:
        errors.append(f"{path}: {t} needs exactly 2 points (has {len(node.points)})")
    elif t == 'Button' and not node.attrs.get('key', '').strip():
        errors.append(f"{path}: Button key is empty")
    elif t == 'Wait':
        try:
            if int(node.attrs.get('timeout', '0')) <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            errors.append(f"{path}: Wait timeout must be a positive integer")
    elif t == 'Condition' and len(node.children) != 2:
        errors.append(f"{path}: Condition must have exactly 2 children")
    elif t == 'meter-list' and not (node.attrs.get('meters') or []):
        errors.append(f"{path}: meter-list has no meters selected")

    if t == 'Random':
        for i, child in enumerate(node.children):
            if child.weight is None:
                errors.append(
                    f"{path}/Random child {i} ({child.node_type}) has no weight — "
                    "may behave unexpectedly"
                )

    for i, child in enumerate(node.children):
        _validate_node(child, errors, f"{path}/{t}[{i}]")


# ═══════════════════════════════════════
#  Template helpers (internal)
# ═══════════════════════════════════════

def _touch(x, y, w=None, comment='') -> RobotNode:
    n = RobotNode.new('Touch-Screen')
    n.points = [[x, y]]
    if w is not None:
        n.weight = w
    n.comment = comment
    return n


def _touch_area(x1, y1, x2, y2, w=None, comment='') -> RobotNode:
    n = RobotNode.new('Touch-Area')
    n.points = [[x1, y1], [x2, y2]]
    if w is not None:
        n.weight = w
    n.comment = comment
    return n


def _wait(timeout, units='Seconds', state='', id_='') -> RobotNode:
    n = RobotNode.new('Wait')
    n.attrs.update({'timeout': str(timeout), 'units': units, 'state': state})
    if id_:
        n.id = id_
    return n


def _button(key='Play', id_='') -> RobotNode:
    n = RobotNode.new('Button')
    n.attrs['key'] = key
    if id_:
        n.id = id_
    return n


def _random(*children, comment='') -> RobotNode:
    n = RobotNode(node_type='Random')
    n.children = list(children)
    n.comment = comment
    return n


def _seq(*children, weight=None, comment='') -> RobotNode:
    n = RobotNode(node_type='Sequence')
    n.children = list(children)
    if weight is not None:
        n.weight = weight
    n.comment = comment
    return n


def _meter_list() -> RobotNode:
    return RobotNode.new('meter-list')


def _footer() -> List[RobotNode]:
    ic = RobotNode.new('Insert-Credit')
    cj = RobotNode(node_type='Clear-Jackpot')
    out = RobotNode.new('output')
    return [ic, cj, out]


# ═══════════════════════════════════════
#  Templates
# ═══════════════════════════════════════

def template_helix_standard() -> RobotNode:
    """3-denom game switching on Helix with yes/no confirmation."""
    inner = _seq(
        _random(
            _touch(765, 740, w=40),
            _touch(950, 740, w=40),
            _touch(1150, 740, w=40),
            comment="choose denomination",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(870, 595, w=95, comment="yes / confirm"),
            _touch(1050, 595, w=5, comment="no / cancel"),
            comment="confirm game switch (95% yes, 5% no)",
        ),
        _wait(2, state='Game-Idle', id_='Wait for idle'),
        _touch(150, 940, w=500, comment="touch games menu — return to lobby"),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        comment="Helix Standard — game switching loop",
    )
    return _seq(_meter_list(), inner, *_footer())


def template_helix_xt() -> RobotNode:
    """Game switching + UPI on Helix XT (4K portrait, doubled coordinates)."""
    inner = _seq(
        _random(
            _touch(3380, 1340, w=40),
            _touch(3380, 1140, w=40),
            _touch(3380, 940, w=40),
            comment="choose denomination (4K doubled coords)",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(3660, 2040, w=40, comment="language switch"),
            _touch(3750, 2150, w=40, comment="volume"),
            _touch(3800, 2040, w=40, comment="game rules"),
            comment="UPI buttons in denom screen",
        ),
        _button(key='Play'),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _touch(3660, 140, w=500, comment="touch games menu — return to lobby"),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(3770, 170, w=40, comment="language switch (lobby)"),
            _touch(3760, 520, w=40, comment="volume (lobby)"),
            _touch(3780, 680, w=40, comment="game rules (lobby)"),
            comment="UPI buttons in lobby",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        comment="Helix XT (4K Portrait) — game switching + UPI",
    )
    return _seq(_meter_list(), inner, *_footer())


def template_game_switch_gamble() -> RobotNode:
    """SGMD GAMPRO Helix — denom switch + UPI + gamble/take-win + games menu."""
    gamble_seq = _seq(
        _touch(1800, 800, comment="gamble button"),
        _random(
            _touch(1400, 600, w=40, comment="black"),
            _touch(525, 600, w=40, comment="red"),
            _touch(425, 720, w=40, comment="heart"),
            _touch(1500, 720, w=40, comment="spade"),
            _touch(620, 720, w=40, comment="diamond"),
            _touch(1300, 720, w=40, comment="club"),
            _touch(1800, 890, w=40, comment="take win from gamble"),
            comment="choose card colour or take win",
        ),
        weight=50, comment="gamble sequence",
    )
    inner = _seq(
        _random(
            _touch(763, 725, w=40),
            _touch(958, 720, w=40),
            _touch(1150, 728, w=40),
            comment="choose denomination",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(130, 965, w=40, comment="language switch"),
            _touch(45, 1030, w=40, comment="volume"),
            _touch(130, 1070, w=40, comment="game rules"),
            comment="UPI buttons in denom screen",
        ),
        _button(key='Play'),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(1800, 890, w=40, comment="take win"),
            gamble_seq,
            comment="take win or gamble",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _touch(1810, 938, w=500, comment="touch games menu — return to lobby"),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        _random(
            _touch(1800, 1055, w=40, comment="language switch (lobby)"),
            _touch(1480, 1055, w=40, comment="volume (lobby)"),
            _touch(1350, 1055, w=40, comment="game rules (lobby)"),
            comment="UPI buttons in lobby",
        ),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        comment="SGMD GAMPRO Helix — denom + UPI + gamble",
    )
    return _seq(_meter_list(), inner, *_footer())


def template_grandstar_auto_bank() -> RobotNode:
    """Grandstar Helix — auto bank setup + 4x Play (credit insert embedded in sequence)."""
    other_seq = _seq(
        _touch(960, 700, comment="Other amount button"),
        _touch_area(535, 330, 1360, 620, comment="numpad entry area"),
        _touch_area(535, 330, 1360, 620),
        _touch_area(535, 330, 1360, 620),
        _touch_area(535, 330, 1360, 620),
        _touch(780, 700, comment="Set limit"),
        weight=30, comment="enter custom amount",
    )
    bank_seq = _seq(
        _touch(500, 1045, comment="bank button (closed state)"),
        _touch(800, 1045, comment="bank button (open state)"),
        _touch(600, 680, comment="Auto button"),
        _random(
            _touch_area(490, 530, 1435, 590, w=60, comment="tap denom amount area"),
            _touch(600, 700, w=10, comment="All"),
            other_seq,
            comment="choose bank amount",
        ),
        comment="auto bank setup",
    )
    ic = RobotNode.new('Insert-Credit')
    inner = _seq(
        bank_seq,
        _button(key='Play'),
        _button(key='Play'),
        _button(key='Play'),
        _button(key='Play'),
        _wait(3, state='Game-Idle', id_='Wait for idle'),
        ic,
        comment="Grandstar — auto bank + 4x Play (credit embedded)",
    )
    out = RobotNode.new('output')
    return _seq(_meter_list(), inner, out)


TEMPLATES = {
    "Helix Standard Play":       template_helix_standard,
    "Helix XT (4K Portrait)":    template_helix_xt,
    "Game Switching with Gamble": template_game_switch_gamble,
    "Grandstar Auto Bank":       template_grandstar_auto_bank,
}
