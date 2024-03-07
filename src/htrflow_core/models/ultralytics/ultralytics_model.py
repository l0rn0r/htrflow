import logging
from pathlib import Path
from typing import Optional

from huggingface_hub import hf_hub_download, list_repo_files
from huggingface_hub.utils import RepositoryNotFoundError


class UltralyticsModel:
    SUPPORTED_MODEL_TYPES = (".pt", ".yaml")
    CONFIG_JSON = "config.json"
    META_MODEL_TYPE = "model"

    @classmethod
    def from_pretrained(
        cls, model: str | Path, cache_dir: str | Path = "./.cache", hf_token: Optional[str] = None
    ) -> Path:
        """Downloads the model file from Hugging Face Hub or loads it from a local path.

        Args:
            model: The model ID on Hugging Face Hub or a local path to the model.
            cache_dir: The directory where model files should be cached.
            hf_token: An optional Hugging Face authentication token.

        Returns:
            The path to the downloaded model file.
        """
        model_path = Path(model)
        try:
            if model_path.exists() and model_path.suffix in cls.SUPPORTED_MODEL_TYPES:
                return model_path
            else:
                try:
                    return cls._download_from_hub(model, cache_dir, hf_token)

                except RepositoryNotFoundError as e:
                    logging.error(f"Could not download files for {model}: {str(e)}")
                    return None

        except Exception as e:
            raise FileNotFoundError(f"Model file or Hugging Face Hub model {model} not found.") from e

    @classmethod
    def _download_from_hub(cls, hf_model_id, hf_token, cache_dir):
        repo_files = list_repo_files(repo_id=hf_model_id, repo_type=cls.META_MODEL_TYPE, token=hf_token)

        if cls.CONFIG_JSON in repo_files:
            _ = hf_hub_download(
                repo_id=hf_model_id,
                filename=cls.CONFIG_JSON,
                repo_type=cls.META_MODEL_TYPE,
                cache_dir=cache_dir,
                token=hf_token,
            )

        model_file = next((f for f in repo_files if any(f.endswith(ext) for ext in cls.SUPPORTED_MODEL_TYPES)), None)

        if not model_file:
            raise ValueError(
                f"No model file of supported type: {cls.SUPPORTED_MODEL_TYPES} found in repository {hf_model_id}."
            )

        return hf_hub_download(
            repo_id=hf_model_id,
            filename=model_file,
            repo_type=cls.META_MODEL_TYPE,
            cache_dir=cache_dir,
            token=hf_token,
        )


if __name__ == "__main__":
    model = UltralyticsModel.from_pretrained(model="ultralyticsplus/yolov8s")
