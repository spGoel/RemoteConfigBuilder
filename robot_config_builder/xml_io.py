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
        if node.attrs.get('mode') == 'socket':
            addr = node.attrs.get('address', '')
            lines.append(f'{pad}<output address="{addr}"/>')
        else:
            fname = node.attrs.get('filename', 'robotlogs/eventsfile.txt')
            append = node.attrs.get('append', 'False')
            lines.append(f'{pad}<output filename="{fname}" append="{append}"/>')
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


def _gen_state_filter(node: RobotNode, indent: int) -> str:
    """Return the <state-list> block, or '' if no state filter is set."""
    sf = node.state_filter
    if not sf or not sf.get('states'):
        return ''
    pad = TAB * indent
    stype = sf.get('type', 'White')
    lines = [f'{pad}<state-list type="{stype}">']
    for s in sf['states']:
        lines.append(f'{pad}{TAB}<state>{s}</state>')
    lines.append(f'{pad}</state-list>')
    return '\n'.join(lines)


def _gen_leaf_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    sf = _gen_state_filter(node, indent + 1)
    if sf:
        return f'{pad}<event {_build_event_attrs(node)}>\n{sf}\n{pad}</event>'
    return f'{pad}<event {_build_event_attrs(node)}/>'


def _gen_touch_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    lines = [f'{pad}<event {_build_event_attrs(node)}>']
    for pt in node.points:
        lines.append(f'{pad}{TAB}<point x="{pt[0]}" y="{pt[1]}"/>')
    sf = _gen_state_filter(node, indent + 1)
    if sf:
        lines.append(sf)
    lines.append(f'{pad}</event>')
    return '\n'.join(lines)


def _gen_container_event(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    lines = [f'{pad}<event {_build_event_attrs(node)}>']
    for child in node.children:
        lines.append(_node_to_str(child, indent + 1))
    sf = _gen_state_filter(node, indent + 1)
    if sf:
        lines.append(sf)
    lines.append(f'{pad}</event>')
    return '\n'.join(lines)


def _gen_meter_list(node: RobotNode, indent: int) -> str:
    pad = TAB * indent
    mode = node.attrs.get('mode', 'periodic')
    fname = node.attrs.get('output_filename', 'robotlogs/eventsfile.txt')
    append = node.attrs.get('output_append', 'False')
    meters = node.attrs.get('meters') or ALL_METERS

    if mode == 'state':
        state    = node.attrs.get('state', 'Game-Idle')
        on_leave = node.attrs.get('on_leave', 'False')
        # only emit on-leave when True; False is the default and omitted
        ol_attr  = ' on-leave="True"' if on_leave == 'True' else ''
        open_tag = f'{pad}<meter-list state="{state}"{ol_attr}>'
    else:
        timeout = node.attrs.get('timeout', '15')
        units   = node.attrs.get('units', 'Seconds')
        open_tag = f'{pad}<meter-list timeout="{timeout}" units="{units}">'

    if node.attrs.get('output_mode') == 'socket':
        output_line = f'{pad}{TAB}<output address="{node.attrs.get("output_address", "")}"/>'
    else:
        fname = node.attrs.get('output_filename', 'robotlogs/eventsfile.txt')
        append = node.attrs.get('output_append', 'False')
        output_line = f'{pad}{TAB}<output filename="{fname}" append="{append}"/>'

    lines = [open_tag, output_line]
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
        if elem.get('address') is not None:
            node.attrs['mode'] = 'socket'
            node.attrs['address'] = elem.get('address', '')
        else:
            node.attrs['mode'] = 'file'
            node.attrs['filename'] = elem.get('filename', 'robotlogs/eventsfile.txt')
            node.attrs['append'] = elem.get('append', 'False')
        return node
    if tag == 'event':
        return _parse_event(elem)
    return None


def _parse_meter_list(elem) -> RobotNode:
    node = RobotNode(node_type='meter-list')
    if elem.get('state') is not None:
        node.attrs['mode'] = 'state'
        node.attrs['state'] = elem.get('state', 'Game-Idle')
        node.attrs['on_leave'] = elem.get('on-leave', 'False')
    else:
        node.attrs['mode'] = 'periodic'
        node.attrs['timeout'] = elem.get('timeout', '15')
        node.attrs['units'] = elem.get('units', 'Seconds')
    node.attrs['output_filename'] = 'robotlogs/eventsfile.txt'
    node.attrs['output_append'] = 'False'
    node.attrs['meters'] = []
    for child in elem:
        if callable(child.tag):
            continue
        if child.tag == 'output':
            if child.get('address') is not None:
                node.attrs['output_mode'] = 'socket'
                node.attrs['output_address'] = child.get('address', '')
            else:
                node.attrs['output_mode'] = 'file'
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
        node.attrs['units'] = elem.get('units', '')   # '' = no units attr = milliseconds
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
        elif child.tag == 'state-list':
            sf_type = child.get('type', 'White')
            sf_states = [
                sc.text.strip()
                for sc in child
                if not callable(sc.tag) and sc.tag == 'state' and sc.text
            ]
            node.state_filter = {'type': sf_type, 'states': sf_states}
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
