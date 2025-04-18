"""
This module holds the base data structures
"""

import logging
import os
import pickle
from abc import ABC, abstractmethod
from itertools import chain
from typing import Generator, Iterable, Iterator, Sequence

import numpy as np

from htrflow import serialization
from htrflow.results import TEXT_RESULT_KEY, RecognizedText, Result, Segment
from htrflow.utils import imgproc
from htrflow.utils.geometry import Bbox, Mask, Point, Polygon, mask2polygon
from htrflow.volume.node import Node


logger = logging.getLogger(__name__)


class ImageNode(Node, ABC):
    parent: "ImageNode | None"
    children: list["ImageNode"]

    def __init__(
        self,
        height: int,
        width: int,
        coord: Point = Point(0, 0),
        polygon: Polygon | None = None,
        mask: Mask | None = None,
        parent: "ImageNode | None" = None,
        label: str | None = None,
    ):
        super().__init__(parent=parent, label=label)
        self.height = height
        self.width = width
        self.coord = coord
        self.bbox = Bbox(0, 0, width, height).move(coord)
        self.mask = mask
        self.polygon = self._compute_polygon(polygon)
        self._image = None

    def _compute_polygon(self, polygon: Polygon | None):
        if polygon:
            if self.parent:
                polygon = polygon.move(self.parent.coord)
            return polygon

        if self.parent and self.parent.mask is not None:
            x, y = self.parent.coord
            cropped_mask = imgproc.crop(self.parent.mask, self.bbox.move((-x, -y)))
            if cropped_mask.any():
                return mask2polygon(cropped_mask).move(self.coord)

        return self.bbox.polygon()

    def __str__(self) -> str:
        s = f"{self.height}x{self.width} node ({self.label}) at ({self.coord.x}, {self.coord.y})"
        if self.text:
            s += f": {self.text}"
        return s

    def clear_images(self):
        for node in self.traverse():
            del node._image
            node._image = None

    def rescale(self, ratio: float):
        """
        Rescale node

        Scales the geometry-related attributes of this node and its children
        by the given factor.

        Arguments:
            ratio: Scale factor.
        """
        self.height = int(self.height * ratio)
        self.width = int(self.width * ratio)
        self.coord = self.coord.rescale(ratio)
        self.bbox = self.bbox.rescale(ratio)
        self.polygon = self.polygon.rescale(ratio)
        if self.mask is not None:
            self.mask = imgproc.rescale_linear(self.mask, ratio)
        if self._image is not None:
            self._image = imgproc.rescale_linear(self._image, ratio)
        for node in self.children:
            node.rescale(ratio)

    @property
    def image(self):
        """The image this node represents"""
        if self._image is None:
            self._image = self._generate_image()
        return self._image

    @abstractmethod
    def _generate_image(self):
        pass

    @property
    def text(self) -> str | None:
        """Text of this region, if available"""
        if text_result := self.get(TEXT_RESULT_KEY):
            return text_result.top_candidate()
        return None

    @property
    def text_result(self) -> RecognizedText | None:
        return self.get(TEXT_RESULT_KEY, None)

    def is_word(self):
        return self.text is not None and self.parent and self.parent.is_line()

    def is_line(self):
        return self.text is not None and self.parent and self.parent.text is None

    def update(self, result: Result):
        """Update node with result"""
        if result.segments:
            self.create_segments(result.segments)
        self.add_data(**result.data)

    def create_segments(self, segments: Sequence[Segment]) -> None:
        """Segment this node"""
        self.children = [SegmentNode(segment, self) for segment in segments]
        del self._image
        self._image = None

    def contains_text(self) -> bool:
        """Return True if this"""
        if self.text is not None:
            return True
        return any(child.contains_text() for child in self.children)

    def has_regions(self) -> bool:
        return all(child.text is None for child in self.children)

    def segments(self) -> "ImageGenerator":
        return ImageGenerator(self.leaves())

    def is_region(self) -> bool:
        return bool(self.children) and not self.text


class SegmentNode(ImageNode):
    """A node representing a segment of a page"""

    segment: Segment
    parent: ImageNode

    def __init__(self, segment: Segment, parent: ImageNode):
        bbox = segment.bbox.move(parent.coord)
        super().__init__(bbox.height, bbox.width, bbox.p1, segment.polygon, segment.mask, parent)
        self.add_data(segment=segment, **segment.data)
        self.segment = segment
        self._image = self._generate_image()

    def _generate_image(self):
        bbox = self.segment.bbox
        mask = self.segment.mask
        img = imgproc.crop(self.parent.image, bbox)
        if mask is not None:
            img = imgproc.mask(img, mask)
        return img

    def rescale(self, ratio):
        super().rescale(ratio)
        self.segment.rescale(ratio)


class PageNode(ImageNode):
    """A node representing a page / input image"""

    def __init__(self, image_path: str):
        self.path = image_path
        label = os.path.splitext(os.path.basename(image_path))[0]
        image = imgproc.read(self.path)
        self.original_shape = image.shape[:2]
        self.ratio = 1
        height, width = self.original_shape
        super().__init__(height, width, label=label)

        self.add_data(
            file_name=os.path.basename(image_path),
            image_path=image_path,
            image_name=label,
        )

    def set_size(self, size: tuple[int, int]) -> None:
        """
        Set the maximum size of the page

        The page is downsized to fit the given dimensions, while keeping
        the original apect ratio. For example, a 1000x800 page would be
        resized to 500x400 given size=(500,500).

        Arguments:
            size: The desired size in pixels as a (max_height, max_width) tuple.
        """
        old_width = self.width
        old_height = self.height
        width_ratio = old_width / size[1]
        height_ratio = old_height / size[0]
        ratio = 1 / max(width_ratio, height_ratio)
        self.rescale(ratio)
        logger.info("Resized %s from (%d, %d) to (%d, %d)", self.label, old_height, old_width, self.height, self.width)

    def to_original_size(self):
        """Restore the page's orginal size"""
        self.set_size(self.original_shape)

    def _generate_image(self):
        ratio = self.width / self.original_shape[1]
        return imgproc.rescale_linear(imgproc.read(self.path), ratio)


class Collection:
    pages: list[PageNode]
    _DEFAULT_LABEL = "untitled_collection"

    def __init__(
        self,
        paths: Sequence[str],
        label: str | None = None,
        label_format: dict[str, str] | None = None,
    ):
        """Initialize collection

        Arguments:
            paths: A list of paths to images
            label: An optional label describing the collection. If not given,
                the label will be set to the input paths' first shared
                parent directory, and if no such directory exists, it will
                default to "untitled_collection".
            label_format: What label format that should be used with this
                collection, as a dictionary of keyword arguments. See
                Node.relabel_levels for options.
        """
        self.pages = paths2pages(paths)
        self.label = label or _common_basename(paths) or Collection._DEFAULT_LABEL
        self._label_format = label_format or {}
        logger.info("Initialized collection '%s' with %d pages", label, len(self.pages))

    def __iter__(self) -> Iterator[PageNode]:
        return iter(self.pages)

    def __getitem__(self, idx) -> ImageNode:
        if isinstance(idx, tuple):
            i, *rest = idx
            return self.pages[i][rest]
        return self.pages[idx]

    def set_size(self, size: tuple[int, int]):
        """
        Set the maximum size of the collection's pages

        The pages are downsized to fit the given dimensions, while keeping
        the original apect ratio. For example, a 1000x800 image would be
        resized to 500x400 given size=(500,500).

        Arguments:
            size: The desired size in pixels as a (max_height, max_width) tuple.
        """
        for page in self:
            page.set_size(size)

    def traverse(self, filter):
        return chain(*[page.traverse(filter) for page in self])

    @classmethod
    def from_directory(cls, path: str) -> "Collection":
        """Initialize a collection from a directory

        Sets the collection label to the directory name.

        Arguments:
            path: A path to a directory of images.
        """
        paths = [os.path.join(path, file) for file in sorted(os.listdir(path))]
        return cls(paths)

    @classmethod
    def from_pickle(cls, path: str) -> "Collection":
        """Initialize a collection from a pickle file

        Arguments:
            path: A path to a previously pickled collection instance
        """
        with open(path, "rb") as f:
            collection = pickle.load(f)

        if not isinstance(collection, Collection):
            raise pickle.UnpicklingError(f"Unpickling {path} did not return a Collection instance.")

        logger.info("Loaded collection '%s' from %s", collection.label, path)
        return collection

    def __str__(self):
        return f"collection label: {self.label}\ncollection tree:\n" + "\n".join(child.tree2str() for child in self)

    def images(self) -> "ImageGenerator":
        """Yields the collection's original input images"""
        return ImageGenerator(page for page in self.pages)

    def segments(self) -> "ImageGenerator":
        """Yield the active segments' images"""
        return ImageGenerator(self.active_leaves())

    def leaves(self) -> Iterator[ImageNode]:
        yield from chain(*[page.leaves() for page in self])

    def active_leaves(self) -> Generator[ImageNode, None, None]:
        """Yield the collection's active leaves

        Here, an "active leaf" is a leaf node whose depth is equal to
        the maximum depth of the tree. In practice, this means that the
        node was segmented in the previous step (or is a fresh PageNode).
        Inactive leaves are leaves that weren't segmented in the
        previous step, and thus are higher up in the tree than the
        other leaves. These should typically not updated in the next
        steps.
        """
        if self.pages:
            max_depth = max(page.max_depth() for page in self)
            for leaf in self.leaves():
                if leaf.depth == max_depth:
                    yield leaf

    def update(self, results: list[Result]) -> None:
        """Update the collection with model results

        Arguments:
            results: A list of results where the i:th result
                corresponds to the collection's i:th active leaf node.
        """
        leaves = list(self.active_leaves())
        if len(leaves) != len(results):
            raise ValueError(f"Size of input ({len(results)}) does not match the size of the tree ({len(leaves)})")

        for leaf, result in zip(leaves, results):
            leaf.update(result)
        self.relabel()

    def save(
        self,
        directory: str = "outputs",
        serializer: str | serialization.Serializer = "alto",
    ) -> None:
        """Save collection

        Arguments:
            directory: Output directory
            serializer: What serializer to use, either a string name (e.g.,
                "alto") or a Serializer instance. See serialization.supported_formats()
                for available string options.
        """
        serialization.save_collection(self, serializer, directory)

    def set_label_format(self, **kwargs):
        self._label_format = kwargs

    def relabel(self):
        for page in self:
            page.relabel_levels(**self._label_format)


class ImageGenerator:
    """A generator with __len__

    Wrapper around `nodes` that provides a generator over the nodes'
    images and implements len(). This way, there is no need to load
    all images into memory at once, but the length of the generator
    is known beforehand (which is typically not the case), which is
    handy in some cases, e.g., when using tqdm progress bars.
    """

    def __init__(self, nodes: Iterable[ImageNode]):
        self._nodes = list(nodes)

    def __iter__(self) -> Iterator[np.ndarray]:
        for _node in self._nodes:
            yield _node.image

    def __len__(self) -> int:
        return len(self._nodes)


def paths2pages(paths: Sequence[str]) -> list[PageNode]:
    """Create PageNodes

    Creates PageNodes from the given paths. Any path pointing to a file
    that cannot be read or interpreted as an image will be ignored.

    Arguments:
        paths: A sequence of paths pointing to image files.

    Returns:
        A list of PageNodes corresponding to the input paths.
    """
    pages = []
    for path in sorted(paths):
        try:
            page = PageNode(path)
        except imgproc.ImageImportError as e:
            logger.warning(e)
            continue
        pages.append(page)
    return pages


def _common_basename(paths: Sequence[str]):
    """Given a sequence of paths, returns the name of their first shared parent directory"""
    if len(paths) > 1:
        return os.path.basename(os.path.commonpath(paths))
    return os.path.basename(os.path.dirname(paths[0]))
