"""
Utilities for downloading models from the Huggingface Hub.
"""

import fnmatch
import os
import string

from huggingface_hub import hf_hub_download, list_repo_files, model_info
from huggingface_hub.constants import HF_HUB_CACHE, HF_HUB_OFFLINE
from huggingface_hub.errors import HFValidationError, LocalEntryNotFoundError
from huggingface_hub.file_download import repo_folder_name


def _fix_mmlab_dict_file(config_path: str, dictionary_path: str) -> None:
    from mmengine.config import Config  # type: ignore

    cfg = Config.fromfile(config_path)
    cfg.dictionary["dict_file"] = dictionary_path
    cfg.model["decoder"]["dictionary"]["dict_file"] = dictionary_path
    cfg.dump(config_path)


def load_mmlabs(model_id: str, config_id: str | None = None, revision: str | None = None) -> tuple[str, str]:
    """Download mmlabs model files if not present in cache.

    OpenMMLabs models need two files: a model weights file and a config file.
    This function finds any cached versions of the given files, or, if necessary,
    downloads the files from the huggingface hub.

    Arguments:
        model_id: Path to a local .pth model weights file or ID of a huggingface
            repo which contains a .pth file.
        config_id: Path to a local config.py file or ID of a huggingface repo which
            contains a config.py file.

    Returns:
        A tuple of paths (model_path, config_path) pointing to the local model
        and config files.
    """
    if os.path.exists(model_id) and config_id and os.path.exists(config_id):
        return model_id, config_id

    model = _hf_hub_download_matching_file(model_id, "*.pth", revision)
    config = _hf_hub_download_matching_file(model_id, "config.py", revision)

    try:
        dictionary = _hf_hub_download_matching_file(model_id, "dictionary.txt", revision)
        _fix_mmlab_dict_file(config, dictionary)
    except FileNotFoundError:
        pass

    return model, config


def load_ultralytics(model_id: str, revision: str | None = None) -> str:
    """Download ultralytics model if it's not present in cache.

    Returns:
        Path to a .pt model file
    """
    if os.path.exists(model_id):
        return model_id
    return _hf_hub_download_matching_file(model_id, "*.pt", revision)


def commit_hash_from_path(path: str) -> str | None:
    """Parse the commit hash from a cached repo file

    Downloads from the huggingface hub end up in a directory named
    as the latest commit hash, like this:
        `my-model/snapshots/<commit hash>/model.pt`

    Arguments:
        path: A path to a cached file from the hugging face hub

    Returns:
        The commit hash if available, else None.
    """
    _, sha = os.path.split(os.path.dirname(path))
    if all(ch in string.hexdigits for ch in sha):
        return sha
    return None


def _hf_hub_download_matching_file(repo_id: str, pattern: str, revision: str | None) -> str:
    """Download file from the given repo based on its filename

    Uses `hf_hub_download` to download the first file in the given repo
    that matches the given pattern. Only downloads the file if it's
    not already present in local cache.

    Arguments:
        repo_id: A huggingface repository ID consisting of a user or
            organization name and a repo name separated by a `/`.
        pattern: The requested filename pattern. The pattern matching is
            based on `fnmatch`.

    Returns:
        The local path to the cached or newly downloaded file.

    Raises:
        FileNotFoundError if no such file exists in the repository,
        or, if in offline mode, no such file is present in the cache.
    """
    repo_files = _list_repo_files(repo_id)
    for file_ in repo_files:
        if fnmatch.fnmatch(file_, pattern):
            return hf_hub_download(repo_id, file_, revision=revision)
    raise FileNotFoundError(
        (
            "Could not find any file that matches the pattern '%s' in "
            "the repo '%s'. If the file does exist, make sure that you are "
            "online and have access to the repo on the huggingface hub or "
            "that the file is available locally at %s."
        )
        % (pattern, repo_id, _cached_repo_path(repo_id))
    )


def _cached_repo_path(repo_id: str) -> str:
    """Returns the path to the cached repository.

    Returns the path to the directory where `hf_hub_download` would
    download the given repository to.
    """
    repo_dir = repo_folder_name(repo_id=repo_id, repo_type="model")
    return os.path.join(HF_HUB_CACHE, repo_dir)


def _list_cached_repo_files(repo_id: str) -> list[str]:
    """List cached files from a given repo.

    Lists all locally available files from the given repo that have
    been downloaded by calls to `hf_hub_download`.

    Arguments:
        repo_id: A huggingface repository ID consisting of a user or
            organization name and a repo name separated by a `/`.

    Returns:
        A list of names of cached files from the given repo.
    """
    path = _cached_repo_path(repo_id)
    return [file for _, _, files in os.walk(path) for file in files]


def _list_repo_files(repo_id: str) -> list[str]:
    """List files in a given huggingface repo.

    A version of huggingface_hub's `list_repo_files` which works in
    offline mode. Whenever `local_files_only` is True, this function
    looks for cached files instead of making a call to the huggingface
    hub.

    Arguments:
        repo_id: A huggingface repository ID consisting of a user or
            organization name and a repo name separated by a `/`.

    Returns:
        A list of all available files in the given repo.
    """
    if HF_HUB_OFFLINE:
        return _list_cached_repo_files(repo_id)
    return list_repo_files(repo_id)


def get_model_info(repo_id: str, revision: str | None = None) -> str | None:
    """
    Get a commit hash from a HF repo ID.

    Arguments:
        repo_id: HF repo ID.
        revision: HF repo revision.

    Returns:
        A commit hash if available, else None.
    """

    # Try to call HF API if we are online
    if not HF_HUB_OFFLINE:
        try:
            return model_info(repo_id, revision=revision).sha
        except HFValidationError:
            return None

    # If we're offline and using a previously cached model, we use the commit
    # hash found in the cached model's path.
    try:
        # `hf_hub_download` returns the path to the last cached version
        cached_path = hf_hub_download(repo_id, "config.json", revision=revision)
        return commit_hash_from_path(cached_path)
    except LocalEntryNotFoundError:
        pass

    return None
