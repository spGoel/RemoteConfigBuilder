import xml.etree.ElementTree as ET
from typing import List, Optional

from models import (
    RobotNode, ALL_METERS, TOUCH_TYPES, LEAF_TYPES,
    ATTR_ORDER, ATTR_KEY_MAP,
)

TAB = "\t"


# ═══════════════════════════════════════
#  XML Generation
# ═══════════════════════════════════════

def generate_xml(root_node: RobotNode) -> str:
    root = _node_to_element(root_node)
    _indent(root)
    xml = ET.tostring(root, encoding='unicode', short_empty_elements=True)
    if root_node.comment:
        comment = ET.tostring(
            ET.Comment(f" {root_node.comment} "), encoding='unicode'
        )
        return f"{comment}\n{xml}"
    return xml


def _node_to_element(node: RobotNode):
    if node.node_type == 'meter-list':
        return _meter_list_element(node)
    if node.node_type == 'output':
        return _output_element(
            node.attrs.get('mode', 'file'),
            node.attrs.get('filename', 'robotlogs/eventsfile.txt'),
            node.attrs.get('append', 'False'),
            node.attrs.get('address', ''),
        )

    attrs = {'type': node.node_type}
    if node.id:
        attrs['id'] = node.id
    if node.weight is not None:
        attrs['weight'] = str(node.weight)
    for key in ATTR_ORDER.get(node.node_type, []):
        value = node.attrs.get(key)
        if value is not None and value != '':
            attrs[ATTR_KEY_MAP.get(key, key)] = str(value)

    element = ET.Element('event', attrs)
    if node.node_type in TOUCH_TYPES:
        for x, y in node.points:
            ET.SubElement(element, 'point', {'x': str(x), 'y': str(y)})
    elif node.node_type not in LEAF_TYPES:
        for child in node.children:
            if child.comment:
                element.append(ET.Comment(f" {child.comment} "))
            element.append(_node_to_element(child))
    _append_state_filter(element, node.state_filter)
    return element


def _output_element(mode: str, filename: str, append: str, address: str):
    if mode == 'socket':
        return ET.Element('output', {'address': str(address)})
    return ET.Element('output', {
        'filename': str(filename), 'append': str(append),
    })


def _meter_list_element(node: RobotNode):
    mode = node.attrs.get('mode', 'periodic')
    if mode == 'state':
        attrs = {'state': str(node.attrs.get('state', 'Game-Idle'))}
        if node.attrs.get('on_leave') == 'True':
            attrs['on-leave'] = 'True'
    else:
        attrs = {
            'timeout': str(node.attrs.get('timeout', '15')),
            'units': str(node.attrs.get('units', 'Seconds')),
        }

    element = ET.Element('meter-list', attrs)
    element.append(_output_element(
        node.attrs.get('output_mode', 'file'),
        node.attrs.get('output_filename', 'robotlogs/eventsfile.txt'),
        node.attrs.get('output_append', 'False'),
        node.attrs.get('output_address', ''),
    ))
    for meter in node.attrs.get('meters') or ALL_METERS:
        ET.SubElement(element, 'meter').text = meter
    return element


def _append_state_filter(element, state_filter: Optional[dict]):
    if not state_filter or not state_filter.get('states'):
        return
    state_list = ET.SubElement(
        element, 'state-list', {'type': str(state_filter.get('type', 'White'))}
    )
    for state in state_filter['states']:
        ET.SubElement(state_list, 'state').text = str(state)


def _indent(element, level: int = 0):
    """Indent an ElementTree in place while retaining Python 3.8 support."""
    whitespace = "\n" + TAB * level
    if len(element):
        if not element.text or not element.text.strip():
            element.text = whitespace + TAB
        for child in element:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = whitespace
    if level and (not element.tail or not element.tail.strip()):
        element.tail = whitespace


# ═══════════════════════════════════════
#  XML Parsing
# ═══════════════════════════════════════

def parse_xml_file(filepath: str) -> RobotNode:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(filepath, parser)
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
    node = RobotNode.new(event_type)
    node.points = []
    node.id = elem.get('id', '')
    raw_w = elem.get('weight')
    node.weight = int(raw_w) if raw_w is not None else None

    for key in ATTR_ORDER.get(event_type, []):
        xml_key = ATTR_KEY_MAP.get(key, key)
        if xml_key in elem.attrib:
            node.attrs[key] = elem.attrib[xml_key]
    if event_type == 'Wait' and 'units' not in elem.attrib:
        node.attrs['units'] = ''  # Missing units means milliseconds.

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
