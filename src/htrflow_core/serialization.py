from __future__ import annotations

import datetime
import os
from typing import TYPE_CHECKING, Iterable, Union

import xmlschema
from jinja2 import Environment, FileSystemLoader

import htrflow_core


if TYPE_CHECKING:
    from htrflow_core.volume import PageNode, RegionNode, Volume


_TEMPLATES_DIR = "src/htrflow_core/templates"   # Path to templates


class Serializer:
    """Serializer base class.

    Each output format is implemented as a subclass to this class.

    Attributes:
        extension: The file extension assigned with this format, for
            example ".txt" or ".xml"
        format_name: The name of this format, for example "alto"
    """

    extension: str
    format_name: str

    def serialize(self, page: PageNode) -> str:
        """Serialize page

        Arguments:
            page: Input page

        Returns:
            A string"""

    def validate(self, doc: str):
        """Validate document"""


class AltoXML(Serializer):
    """Alto XML serializer"""

    extension = ".xml"
    format_name = "alto"

    def __init__(self):
        env = Environment(loader=FileSystemLoader([_TEMPLATES_DIR, "."]))
        self.template = env.get_template('alto')
        self.schema = "http://www.loc.gov/standards/alto/v4/alto-4-4.xsd"

    def serialize(self, page: PageNode) -> str:
        # ALTO doesn't support nesting of regions ("TextBlock" elements)
        # This function is called from within the jinja template to tell
        # if a node corresponds to a TextBlock element, i.e. if its
        # children contains text and not other regions.
        def is_text_block(node):
            return bool(node.children) and all(child.is_line() for child in node.children)

        return self.template.render(
            page=page,
            metadata=metadata(page),
            labels=label_nodes(page),
            is_text_block=is_text_block
        )

    def validate(self, doc: str):
        xmlschema.validate(doc, self.schema)


class PageXML(Serializer):

    extension = ".xml"
    format_name = "page"

    def __init__(self):
        env = Environment(loader=FileSystemLoader([_TEMPLATES_DIR, "."]))
        self.template = env.get_template("page")
        self.schema = "https://www.primaresearch.org/schema/PAGE/gts/pagecontent/2019-07-15/pagecontent.xsd"

    def serialize(self, page: PageNode):
        return self.template.render(
            page=page,
            metadata=metadata(page),
            labels=label_nodes(page),
        )

    def validate(self, doc: str):
        xmlschema.validate(doc, self.schema)


class PlainText(Serializer):
    extension = ".txt"
    format_name = "txt"

    def serialize(self, page: PageNode) -> str:
        lines = page.traverse(lambda node: node.is_leaf())
        return "\n".join(line.text for line in lines)


def metadata(page: PageNode) -> dict[str, Union[str, list[dict[str, str]]]]:
    """Generate metadata for `page`

    Args:
        page: input page

    Returns:
        A dictionary with metadata
    """
    timestamp = datetime.datetime.utcnow().isoformat()
    return {
        "creator": f"{htrflow_core.__author__}",
        "software_name": f"{htrflow_core.__package_name__}",
        "software_version": f"{htrflow_core.__version__}",
        "application_description": f"{htrflow_core.__desc__}",
        "created": timestamp,
        "last_change": timestamp,
        "processing_steps": [{"description": "", "settings": ""}]
    }


def supported_formats():
    """The supported formats"""
    return [cls.format_name for cls in Serializer.__subclasses__()]


def _get_serializer(format_name):
    for cls in Serializer.__subclasses__():
        if cls.format_name.lower() == format_name.lower():
            return cls()
    msg = f"Format '{format_name}' is not among the supported formats: {supported_formats()}"
    raise ValueError(msg)


def save_volume(volume: Volume, format_: str, dest: str) -> Iterable[tuple[str, str]]:
    """Serialize and save volume

    Arguments:
        volume: Input volume
        format_: What format to use, as a string. See serialization.supported_formats()
            for supported formats.
        dest: Output directory
    """

    serializer = _get_serializer(format_)

    dest = os.path.join(dest, volume.label)
    os.makedirs(dest, exist_ok=True)

    for page in volume:
        if not page.contains_text():
            raise ValueError(f'Cannot serialize page without text: {page.image_name}')

        doc = serializer.serialize(page)
        filename = os.path.join(dest, page.image_name + serializer.extension)

        with open(filename, 'w') as f:
            f.write(doc)


def label_nodes(node: PageNode | RegionNode, template = '%s') -> dict[PageNode | RegionNode, str]:
    """Assign labels to node and its decendents

    Arguments:
        node: Start node
        template: Label template
    """
    labels = {}
    labels[node] = template % node.label
    for i, child in enumerate(node.children):
        labels |= label_nodes(child, f'{labels[node]}_%s{i}')
    return labels
