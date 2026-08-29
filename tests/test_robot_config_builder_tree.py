import random
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILDER_DIR = PROJECT_ROOT / "robot_config_builder"
sys.path.insert(0, str(BUILDER_DIR))

from models import ALL_EVENT_TYPES, CONTAINER_TYPES, RobotNode  # noqa: E402
from tree_panel import TreePanel  # noqa: E402
import xml_io  # noqa: E402


def walk_nodes(root):
    result = []

    def walk(node):
        result.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    return result


def assert_tree_integrity(test_case, root):
    """Assert that the model is a tree, rather than a cyclic/shared graph."""
    seen = set()

    def walk(node, ancestors):
        identity = id(node)
        test_case.assertNotIn(identity, ancestors, "cycle detected")
        test_case.assertNotIn(identity, seen, "node is owned by multiple parents")
        seen.add(identity)
        next_ancestors = ancestors | {identity}
        for child in node.children:
            test_case.assertIsInstance(child, RobotNode)
            walk(child, next_ancestors)

    walk(root, set())
    return len(seen)


class HeadlessTreePanel:
    """Runs TreePanel's mutation code without requiring a display server."""

    _parent_node = TreePanel._parent_node
    _child_index = staticmethod(TreePanel._child_index)
    _is_ancestor = TreePanel._is_ancestor
    _perform_drop = TreePanel._perform_drop
    _add_child = TreePanel._add_child
    _add_sibling = TreePanel._add_sibling
    duplicate_selected = TreePanel.duplicate_selected
    delete_selected = TreePanel.delete_selected
    move_selected = TreePanel.move_selected

    def __init__(self, root):
        self.root_node = root
        self.node_map = {}
        self.selection = root
        self.undo_count = 0
        self.change_count = 0
        self.refresh_tree(root)

    def refresh_tree(self, select_node=None):
        self.node_map = {str(id(node)): node for node in walk_nodes(self.root_node)}
        if select_node is not None:
            self.selection = select_node

    def selected_node(self):
        return self.selection

    def _push_undo(self):
        self.undo_count += 1

    def on_tree_changed(self):
        self.change_count += 1


class FakeTreeview:
    def __init__(self, rows):
        # rows: [(top_y, height, node), ...]
        self.rows = rows

    def identify_row(self, y):
        for top, height, node in self.rows:
            if top <= y < top + height:
                return str(id(node))
        return ""

    def bbox(self, iid):
        for top, height, node in self.rows:
            if str(id(node)) == iid:
                return (0, top, 100, height)
        return ()


class DragEventHarness(HeadlessTreePanel):
    _drag_start = TreePanel._drag_start
    _drag_motion = TreePanel._drag_motion
    _drag_release = TreePanel._drag_release

    def __init__(self, root, rows):
        super().__init__(root)
        self.tree = FakeTreeview(rows)
        self._drag_src_iid = None
        self._drag_src_node = None
        self._drop_target_iid = None
        self._drop_position = None

    def _show_drop_indicator(self, target_iid, position, _bbox):
        self._drop_target_iid = target_iid
        self._drop_position = position

    def _clear_drop_indicator(self):
        self._drop_target_iid = None
        self._drop_position = None


class IdentitySafeMutationTests(unittest.TestCase):
    def test_dragging_one_of_two_equal_subtrees_moves_the_selected_identity(self):
        first_child = RobotNode.new("Wait")
        second_child = RobotNode.new("Wait")
        first = RobotNode("Sequence", children=[first_child])
        second = RobotNode("Sequence", children=[second_child])
        target = RobotNode.new("Sequence")
        root = RobotNode("Sequence", children=[first, second, target])
        panel = HeadlessTreePanel(root)

        # These nodes compare equal by dataclass value, which triggered the
        # original wrong-parent/wrong-removal defect.
        self.assertEqual(first_child, second_child)
        self.assertIs(panel._parent_node(second_child), second)

        panel._perform_drop(second_child, target, "into")

        self.assertEqual(len(first.children), 1)
        self.assertIs(first.children[0], first_child)
        self.assertEqual(second.children, [])
        self.assertEqual(len(target.children), 1)
        self.assertIs(target.children[0], second_child)
        self.assertEqual(assert_tree_integrity(self, root), 6)

    def test_same_parent_before_after_drops_have_exact_order(self):
        nodes = [RobotNode("Wait", id=label) for label in "ABCD"]
        root = RobotNode("Sequence", children=nodes.copy())
        panel = HeadlessTreePanel(root)

        panel._perform_drop(nodes[0], nodes[2], "after")
        self.assertEqual([node.id for node in root.children], ["B", "C", "A", "D"])

        panel._perform_drop(nodes[3], nodes[1], "before")
        self.assertEqual([node.id for node in root.children], ["D", "B", "C", "A"])
        self.assertEqual(assert_tree_integrity(self, root), 5)

    def test_manual_commands_target_the_selected_equal_node(self):
        first = RobotNode.new("Wait")
        second = RobotNode.new("Wait")
        root = RobotNode("Sequence", children=[first, second])
        panel = HeadlessTreePanel(root)
        panel.selection = second

        panel._add_sibling("Button")
        added = root.children[2]
        self.assertIs(root.children[0], first)
        self.assertIs(root.children[1], second)
        self.assertEqual(added.node_type, "Button")

        panel.selection = second
        panel.duplicate_selected()
        duplicate = root.children[2]
        self.assertIsNot(duplicate, second)
        self.assertEqual(duplicate, second)

        panel.selection = second
        panel.move_selected(-1)
        self.assertIs(root.children[0], second)
        self.assertIs(root.children[1], first)

        panel.selection = first
        with patch("tree_panel.messagebox.askyesno", return_value=True):
            panel.delete_selected()
        self.assertTrue(all(node is not first for node in root.children))
        self.assertTrue(any(node is second for node in root.children))
        assert_tree_integrity(self, root)

    def test_invalid_drops_are_non_destructive(self):
        child = RobotNode.new("Wait")
        container = RobotNode("Sequence", children=[child])
        leaf = RobotNode.new("Button")
        root = RobotNode("Sequence", children=[container, leaf])
        panel = HeadlessTreePanel(root)
        original_ids = [id(node) for node in walk_nodes(root)]

        panel._perform_drop(container, child, "into")  # cycle
        panel._perform_drop(child, leaf, "into")       # leaf target
        panel._perform_drop(child, root, "before")     # relative to root
        panel._perform_drop(child, container, "sideways")

        self.assertEqual([id(node) for node in walk_nodes(root)], original_ids)
        self.assertEqual(panel.undo_count, 0)
        assert_tree_integrity(self, root)

    def test_root_row_is_always_an_into_drop_target(self):
        source = RobotNode.new("Wait")
        root = RobotNode("Sequence", children=[source])
        panel = DragEventHarness(root, [(0, 20, root), (20, 20, source)])
        panel._drag_src_iid = str(id(source))
        panel._drag_src_node = source

        # This is the top edge, which previously meant invalid "before root".
        panel._drag_motion(SimpleNamespace(y=1))

        self.assertEqual(panel._drop_target_iid, str(id(root)))
        self.assertEqual(panel._drop_position, "into")

    def test_release_coordinates_override_a_stale_motion_target(self):
        first = RobotNode("Button", id="first")
        actual_target = RobotNode("Button", id="actual")
        source = RobotNode("Wait", id="source")
        root = RobotNode("Sequence", children=[first, actual_target, source])
        panel = DragEventHarness(
            root,
            [(0, 20, first), (20, 20, actual_target), (40, 20, source)],
        )
        panel._drag_src_iid = str(id(source))
        panel._drag_src_node = source
        panel._drop_target_iid = str(id(first))
        panel._drop_position = "before"

        panel._drag_release(SimpleNamespace(y=21))

        self.assertEqual(
            [node.id for node in root.children],
            ["first", "source", "actual"],
        )
        assert_tree_integrity(self, root)


class XmlCreationLoadTests(unittest.TestCase):
    def test_every_manual_event_type_generates_parseable_xml(self):
        root = RobotNode.new("Sequence")
        panel = HeadlessTreePanel(root)

        for event_type in ALL_EVENT_TYPES:
            panel.selection = root
            panel._add_child(event_type)

        self.assertEqual(
            [node.node_type for node in root.children],
            ALL_EVENT_TYPES,
        )
        ET.fromstring(xml_io.generate_xml(root))
        self.assertEqual(assert_tree_integrity(self, root), len(ALL_EVENT_TYPES) + 1)

    def test_2000_mixed_manual_and_drag_drop_operations(self):
        rng = random.Random(0xC0FFEE)
        root = RobotNode.new("Sequence")
        panel = HeadlessTreePanel(root)

        # Seed many identical default nodes: this is the important real-world
        # case because users commonly add several Wait/Button/Sequence rows.
        for _ in range(40):
            panel.selection = root
            panel._add_child(rng.choice(ALL_EVENT_TYPES))

        for operation_number in range(2000):
            nodes = walk_nodes(root)
            non_root = nodes[1:]
            containers = [node for node in nodes if node.node_type in CONTAINER_TYPES]

            if len(nodes) < 80:
                action = rng.choice(["add_child", "add_sibling", "drop"])
            elif len(nodes) > 300:
                action = rng.choice(["delete", "drop", "move"])
            else:
                action = rng.choice([
                    "add_child", "add_sibling", "duplicate", "delete",
                    "move", "drop", "drop", "drop",
                ])

            before_count = len(nodes)
            if action == "add_child":
                panel.selection = rng.choice(containers)
                panel._add_child(rng.choice(ALL_EVENT_TYPES))
            elif action == "add_sibling" and non_root:
                panel.selection = rng.choice(non_root)
                panel._add_sibling(rng.choice(ALL_EVENT_TYPES))
            elif action == "duplicate" and non_root:
                # Keep growth bounded while still exercising equal copies.
                candidates = [node for node in non_root if not node.children]
                if candidates:
                    panel.selection = rng.choice(candidates)
                    panel.duplicate_selected()
            elif action == "delete" and len(non_root) > 20:
                panel.selection = rng.choice(non_root)
                with patch("tree_panel.messagebox.askyesno", return_value=True):
                    panel.delete_selected()
            elif action == "move" and non_root:
                panel.selection = rng.choice(non_root)
                panel.move_selected(rng.choice([-1, 1]))
            elif action == "drop" and len(non_root) > 1:
                source = rng.choice(non_root)
                target = rng.choice([node for node in nodes if node is not source])
                panel._perform_drop(source, target, rng.choice(["before", "into", "after"]))
                self.assertEqual(
                    len(walk_nodes(root)),
                    before_count,
                    f"drag/drop changed node count at operation {operation_number}",
                )

            node_count = assert_tree_integrity(self, root)
            self.assertEqual(node_count, len(walk_nodes(root)))
            if operation_number % 25 == 0:
                ET.fromstring(xml_io.generate_xml(root))

        xml_text = xml_io.generate_xml(root)
        ET.fromstring(xml_text)
        round_tripped = xml_io._parse_element(ET.fromstring(xml_text))
        self.assertEqual(len(walk_nodes(round_tripped)), len(walk_nodes(root)))

    def test_bundled_xml_files_load_and_regenerate(self):
        files = [BUILDER_DIR / "default.xml", *(BUILDER_DIR / "templates").glob("*.xml")]
        self.assertGreater(len(files), 1)
        for path in files:
            with self.subTest(path=path.name):
                root = xml_io.parse_xml_file(str(path))
                ET.fromstring(xml_io.generate_xml(root))
                assert_tree_integrity(self, root)


if __name__ == "__main__":
    unittest.main()
