#!/usr/bin/env python

# This script is used to identify *.nf.test files for changed functions/processs/workflows/pipelines and *.nf-test files
# with changed dependencies, then return as a JSON list

import argparse
import json
import logging
import os
import re
import yaml

from git import Repo
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments and return an ArgumentParser object.

    Returns:
        argparse.ArgumentParser: The ArgumentParser object with the parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Scan *.nf.test files for function/process/workflow name and return as a JSON list"
    )
    parser.add_argument(
        "-p",
        "--path",
        help="Path to scan for nf-test files. Should be root of repository.",
        default=".",
    )
    parser.add_argument(
        "-r",
        "--head_ref",
        required=True,
        help="Head reference branch (Source branch for a PR).",
    )
    parser.add_argument(
        "-b",
        "--base_ref",
        required=True,
        help="Base reference branch (Target branch for a PR).",
    )
    parser.add_argument(
        "-x",
        "--ignored_files",
        nargs="+",
        default=[
            ".git/*",
            ".gitpod.yml",
            ".prettierignore",
            ".prettierrc.yml",
            "*.md",
            "*.png",
            "modules.json",
            "pyproject.toml",
            "tower.yml",
        ],
        help="List of files or file substrings to ignore.",
    )
    parser.add_argument(
        "-i",
        "--include",
        type=Path,
        default=None,
        help="Path to an include file containing a YAML of key value pairs to include in changed files. I.e., return the current directory if an important file is changed.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        type=str,
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "-t",
        "--types",
        type=str,
        default="function,process,workflow,pipeline",
        help="Types of tests to include.",
    )
    parser.add_argument(
        "-n",
        "--n_parents",
        type=int,
        default=0,
        help="Number of parents to up to return. 0 for file, 1 for immediate dir, 2 for parent dir, etc.",
    )
    return parser.parse_args()


def read_yaml_inverted(file_path: str) -> dict:
    """
    Read a YAML file and return its contents as a dictionary but reversed, i.e. the values become the keys and the keys become the values.

    Args:
        file_path (str): The path to the YAML file.

    Returns:
        dict: The contents of the YAML file as a dictionary inverted.
    """
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)

    # Invert dictionary of lists into contents of lists are keys, values are the original keys
    # { "key": ["item1", "item2] } --> { "item1": "key", "item2": "key" }
    return {value: key for key, values in data.items() for value in values}


def find_changed_files(
    path: Path,
    branch1: str,
    branch2: str,
    ignore: list[str],
) -> list[Path]:
    """
    Find all *.nf.tests that are associated with files that have been changed between two specified branches.

    Args:
        repo (Path)        : Path to the repository to scan.
        branch1 (str)      : The first branch being compared
        branch2 (str)      : The second branch being compared
        ignore  (list)     : List of files or file substrings to ignore.

    Returns:
        list: List of files matching the pattern *.nf.test that have changed between branch2 and branch1.
    """

    # Initialise repo for scanning
    repo = Repo(path)

    # identify commit on branch1
    branch1_commit = repo.commit(branch1)
    # identify commit on branch2
    branch2_commit = repo.commit(branch2)
    # compare two branches
    diff_index = branch1_commit.diff(branch2_commit)

    # Start empty list of changed files
    changed_files = []

    # For every file that has changed between commits
    for file in diff_index:
        # Get pathlib.Path object
        filepath = Path(file.a_path)
        # If file does not match any in the ignore list, add containing directory to changed_files
        if not any(filepath.match(ignored_path) for ignored_path in ignore):
            # Prepend the root of the path for better scanning
            changed_files.append(path.joinpath(filepath))

    # Uniqueify the results before returning for efficiency
    return list(set(changed_files))


def detect_include_files(
    changed_files: list[Path], include_files: dict[str, str]
) -> list[Path]:
    """
    Detects the include files based on the changed files.

    Args:
        changed_files (list[Path]): List of paths to the changed files.
        include_files (dict[str, str]): Key-value pairs to return if a certain file has changed. If a file in a directory has changed, it points to a different directory.

    Returns:
        list[Path]: List of paths to representing the keys of the include_files dictionary, where a value matched a path in changed_files.
    """
    new_changed_files = []
    for filepath in changed_files:
        # If file is in the include_files, we return the key instead of the value
        for include_path, include_key in include_files.items():
            if filepath.match(include_path):
                new_changed_files.append(Path(include_key))
    return new_changed_files


def detect_files(paths: list[Path], suffix: str) -> list[Path]:
    """
    Detects and returns a list of nf-test files from the given list of changed files.

    Args:
        paths (list[Path]): A list of file paths to scan.
        suffix (str): File suffix to detect

    Returns:
        list[Path]: A list of nf-test file paths.
    """
    result: list[Path] = []

    for path in paths:
        # If Path is the exact nf-test file add to list:
        if path.match(suffix) and path.exists():
            result.append(path)
        # Else recursively search for nf-test files:
        elif path.is_dir():
            # Search the dir
            # e.g.
            # dir/
            # ├─ main.nf
            # ├─ main.nf.test
            for file in path.rglob(suffix):
                result.append(file)
        elif path.is_file():
            # Search the enclosing dir so files in the same dir can be found.
            # e.g.
            # dir/
            # ├─ main.nf
            # ├─ main.nf.test
            for file in path.parent.rglob(suffix):
                result.append(file)

    return result


def convert_nf_test_files_to_test_types(
    files: list[Path],
    types: list[str] = ["function", "process", "workflow", "pipeline"],
) -> tuple[dict[str, list[str]], dict[str, list[Path]]]:
    """
    Converts Nextflow test files to test types and returns as two identical dicts, one with test targets and one with the paths

    Args:
        files (list[Path]): A list of file paths to Nextflow test files.
        types (list[str], optional): A list of test types to consider. Defaults to ["function", "process", "workflow", "pipeline"].

    Returns:
        tuple[dict[str, list[str]], dict[str, list[Path]]]: A tuple containing two dictionaries:
            - result_names: A dictionary mapping test types to a list of test names.
            - result_files: A dictionary mapping test types to a list of file paths.

    """
    # Populate empty dict from types
    result_names: dict[str, list[str]] = {key: [] for key in types}
    result_files: dict[str, list[Path]] = {key: [] for key in types}

    for file in files:
        with open(file, "r") as f:
            testtype, name = find_test_type(f.readlines())

            if testtype in types:
                result_names[testtype].append(name)
                result_files[testtype].append(file)
            # As a safety measure and future proofing update the dict with any missing vals
            else:
                result_names.update({testtype: [name]})
                result_files.update({testtype: [file]})

    return result_names, result_files


def find_run_statements(lines: list[str]) -> list[str]:
    """
    Find all run statements in a list of lines.

    Args:
        lines (list): List of lines to scan.

    Returns:
        list: List of run statements.
    """
    result = []
    for line in lines:
        if line.strip().startswith("run"):
            # This parses `run("<tool>")` to `<tool>
            dependency = (
                line.strip()
                .split()[0]
                .lstrip("run(")
                .rstrip(")")
                .strip("\"'")
                .casefold()
            )
            result.append(dependency)
    return result


def find_include_statements(lines: list[str]) -> list[str]:
    """
    Find all include statements in a list of lines.

    Args:
        lines (list): List of lines to scan.

    Returns:
        list: List of include statements.
    """
    result = []
    for line in lines:
        if "include {" in line:
            dependency = line.split()[2].strip("'\"").replace("/", "_").casefold()
            result.append(dependency)
    return result


def find_test_type(lines: list[str]) -> tuple[str, str]:
    """
    Finds the test type keyword and name from a list of lines.

    Args:
        lines (list[str]): The list of lines to search for the test type.

    Returns:
        tuple(str, str): A tuple containing the test type keyword and name.

    """
    for line in lines:
        words = line.split()
        if (
            line.strip().startswith(("workflow", "process", "function"))
            and len(words) == 2
            and re.match(r'^".*"$', words[1]) is not None
        ):
            keyword = words[0]
            name = words[1].strip("'\"")  # Strip both single and double quotes
            return (keyword, name)
    return ("pipeline", "PIPELINE")


def find_nf_tests_with_changed_dependencies(
    paths: list[Path], tags: list[str]
) -> list[Path]:
    """
    Find all *.nf.test files with changed dependencies
    (identified as modules loaded in the via setup { run("<tool>") } from a list of paths.

    Args:
        paths (list): List of directories or files to scan.
        tags (list): List of tags identified as having changes.

    Returns:
        list: List of *.nf.test files with changed dependencies.
    """

    result: list[Path] = []

    nf_test_files = detect_files(paths, "*.nf.test")

    # find nf-test files with changed dependencies
    for nf_test_file in nf_test_files:
        with open(nf_test_file, "r") as f:
            lines = f.readlines()
            # Get all tags from nf-test file
            # Make case insensitive with .casefold()
            tags_in_nf_test_file = find_run_statements(lines)
            # Check if tag in nf-test file appears in a tag.
            # Use .casefold() to be case insensitive
            if any(
                tag.casefold().replace("/", "_") in tags_in_nf_test_file for tag in tags
            ):
                result.append(nf_test_file)

    return result


def find_nf_files_with_changed_dependencies(
    paths: list[Path], tags: list[str]
) -> list[Path]:
    """
    Find all *.nf.test files with where the *.nf file uses changed dependencies
    (identified via include { <tool> }) in *.nf files from a list of paths.

    Args:

        paths (list): List of directories or files to scan.
        tags (list): List of tags identified as having changes.

    Returns:
        list: List of *.nf.test files from *.nf files with changed dependencies.
    """

    nf_files_w_changed_dependencies: list[Path] = []

    nf_files = detect_files(paths, "*.nf")

    # find nf files with changed dependencies
    for nf_file in nf_files:
        with open(nf_file, "r") as f:
            lines = f.readlines()
            # Get all include statements from nf file
            # Make case insensitive with .casefold()
            includes_in_nf_file = find_include_statements(lines)
            # Check if include in nf file appears in a tag.
            # Use .casefold() to be case insensitive
            if any(
                tag.casefold().replace("/", "_") in includes_in_nf_file for tag in tags
            ):
                nf_files_w_changed_dependencies.append(nf_file)

    # find nf-test for nf files with changed dependencies
    nf_test_files_for_changed_dependencies = detect_files(
        nf_files_w_changed_dependencies, "*.nf.test"
    )

    return nf_test_files_for_changed_dependencies


def get_target_tests(results: dict[str, list[any]], types: list[str]) -> list[any]:
    """
    Returns a list of target tests based on the given results and types.

    Args:
        results (dict[str, list[str]]): A dictionary containing test results for different types.
        types (list[str]): A list of target types.

    Returns:
        list[str]: A list of target tests.

    """
    target_results: list[str] = []

    for target in types:
        target_results = target_results + results.get(target, [])

    return target_results


def get_parents(path: Path, n: int) -> Path:
    """
    Get the parent directory of a path n levels up.

    Args:
        path (Path): The path to get the parent of.
        n (int): The number of levels to go up.

    Returns:
        Path: The parent directory n levels up.
    """
    parent = path
    for _ in range(n):
        parent = parent.parent
    return parent


if __name__ == "__main__":

    # Utility stuff
    args = parse_args()
    logging.basicConfig(level=args.log_level)
    # Argparse handling of nargs is a bit rubbish. So we do it manually here.
    args.types = args.types.split(",")
    # Quick validation of args.types since we cant do this in argparse
    if any(
        _type not in ["function", "process", "workflow", "pipeline"]
        for _type in args.types
    ):
        raise ValueError(
            f"Invalid test type specified. Must be one of 'function', 'process', 'workflow', 'pipeline'. Found: {args.types}"
        )

    root_path = Path(args.path)
    modules_path = Path(root_path, "modules")
    subworkflows_path = Path(root_path, "subworkflows")
    workflows_path = Path(root_path, "workflows")

    # Parse nf-test files for target test tags
    changed_files = find_changed_files(
        root_path, args.head_ref, args.base_ref, args.ignored_files
    )

    # If an additional include YAML is added, we detect additional changed dirs to include
    if args.include:
        include_files = read_yaml_inverted(args.include)
        changed_files = changed_files + detect_include_files(
            changed_files, include_files
        )
    nf_test_files = detect_files(changed_files, "*.nf.test")
    changed_component_names, changed_component_files = (
        convert_nf_test_files_to_test_types(nf_test_files)
    )

    # Get only relevant results (specified by -t)
    target_tests = get_target_tests(changed_component_names, args.types)
    target_test_paths = get_target_tests(changed_component_files, args.types)

    # Parse nf-test files to identify nf-tests containing "setup" with changed module/subworkflow/workflow
    nf_test_changed_setup = find_nf_tests_with_changed_dependencies(
        [modules_path, subworkflows_path, workflows_path],
        target_tests,
    )

    # Parse *.nf files to identify nf-files containing include with changed module/subworkflow/workflow
    nf_files_changed_include = find_nf_files_with_changed_dependencies(
        [modules_path, subworkflows_path, workflows_path],
        target_tests,
    )

    # Get union of all test files
    all_nf_tests = list(
        {
            get_parents(test_path, args.n_parents)
            for test_path in target_test_paths
            + nf_test_changed_setup
            + nf_files_changed_include
        }
    )

    # Remove root from path and stringify
    normalised_nf_tests = [
        str(test_path.relative_to(root_path)) for test_path in all_nf_tests
    ]

    # Print to string for outputs
    output_string = json.dumps(normalised_nf_tests)

    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(
                f"components={output_string}",
                file=f,
            )

    print(output_string)
