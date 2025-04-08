import logging
import re
from functools import lru_cache
from typing import Any

import numpy as np
from transformers import DonutProcessor, VisionEncoderDecoderModel

from htrflow.models.base_model import BaseModel
from htrflow.models.download import get_model_info
from htrflow.results import Result


logger = logging.getLogger(__name__)


class Donut(BaseModel):
    """
    HTRflow adapter of Donut model.
    """

    def __init__(self,
        model: str,
        processor: str | None = None,
        model_kwargs: dict[str, Any] | None = None,
        processor_kwargs: dict[str, Any] | None = None,
        prompt: str = "<s>",
        **kwargs,
    ):
        """
        Arguments:
            model: Path or name of pretrained VisisonEncoderDeocderModel.
            processor: Optional path or name of pretrained DonutProcessor. If not given, the model path or name is
                used.
            model_kwargs: Model initialization kwargs which are forwarded to
                VisionEncoderDecoderModel.from_pretrained.
            processor_kwargs: Processor initialization kwargs which are forwarded to DonutProcessor.from_pretrained.
            prompt: Task prompt to use for all decoding tasks. Defaults to '<s>'.
            kwargs: Additional kwargs which are forwarded to BaseModel's __init__.
        """
        super().__init__(**kwargs)

        # Initialize model
        model_kwargs = model_kwargs or {}
        self.model = VisionEncoderDecoderModel.from_pretrained(model, **model_kwargs)
        self.model.to(self.device)
        logger.info("Initialized Donut model from %s on device %s.", model, self.model.device)

        # Initialize processor
        processor = processor or model
        processor_kwargs = processor_kwargs or {}
        self.processor = DonutProcessor.from_pretrained(processor, **processor_kwargs)
        logger.info("Initialized Donut processor from %s.", processor)

        self.prompt = prompt

        self.metadata["model"] = model
        self.metadata["model_version"] = get_model_info(model, model_kwargs.get("revision", None))
        self.metadata["processor"] = processor
        self.metadata["processor_version"]= get_model_info(processor, processor_kwargs.get("revision", None))

    def _predict(self, images: list[np.ndarray], **generation_kwargs) -> list[Result]:

        # Prepare generation kwargs
        defaults = {
            "max_length": self.model.decoder.config.max_position_embeddings,
            "pad_token_id": self.processor.tokenizer.pad_token_id,
            "eos_token_id": self.processor.tokenizer.eos_token_id,
            "bad_words_ids": [[self.processor.tokenizer.unk_token_id]],
        }
        overrides = {
            "return_dict_in_generate": True,
            "output_scores": True,
        }
        generation_kwargs = defaults | generation_kwargs | overrides
        warn_when_overridden(generation_kwargs, overrides)

        # Run inference
        prompts = [self.prompt for _ in images]
        pixel_values = self.processor(images, prompts, return_tensors="pt").pixel_values
        outputs = self.model.generate(pixel_values.to(self.model.device), **generation_kwargs)

        # Construct results
        results = []
        for sequence in self.processor.batch_decode(outputs.sequences):
            sequence = sequence.replace(self.processor.tokenizer.eos_token, "")
            sequence = sequence.replace(self.processor.tokenizer.pad_token, "")
            sequence = re.sub(r"<.*?>", "", sequence, count=1).strip()
            data = self.processor.token2json(sequence)
            results.append(Result(data=data))
        return results


def warn_when_overridden(kwargs: dict, overrides: dict):
    """
    Log a warning if any of the given keyword arguments are overridden.

    Arguments:
        kwargs: Given keyword arguments.
        overrides: Keyword argument overrides.
    """
    for key, value in kwargs.items():
        if key in overrides:
            if overrides[key] != value:
                msg = "HTRflow Donut model does not support '%s'='%s'. Using '%s'='%s' instead."
                _warn_once(msg, key, value, overrides[key])

@lru_cache
def _warn_once(msg, *args):
    """Log `msg` once"""
    logger.warning(msg, *args)
