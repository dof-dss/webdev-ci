"""Offline compiler for the supported shared application CircleCI subset.

This deliberately rejects unfamiliar syntax instead of claiming CircleCI parity.
No shell, network, Docker, or Git commands run during compilation.
"""

from __future__ import annotations

import base64
import copy
import gzip
import io
from pathlib import Path
import re
import tarfile

import yaml


REFERENCE = re.compile(r"<<\s*([^<>]+?)\s*>>")
NATIVE_STEPS = {
    "checkout", "run", "add_ssh_keys", "persist_to_workspace",
    "attach_workspace", "restore_cache", "save_cache",
}
STEP_OPTIONS = {
    "checkout": set(),
    "run": {"name", "command", "shell", "environment", "working_directory", "no_output_timeout", "when"},
    "add_ssh_keys": {"fingerprints"},
    "persist_to_workspace": {"root", "paths"},
    "attach_workspace": {"at"},
    "restore_cache": {"key", "keys"},
    "save_cache": {"key", "paths"},
}


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unsupported {label} keys: {sorted(unknown)}")


def _parameters(declarations, supplied, label):
    if not isinstance(declarations, dict) or not isinstance(supplied, dict):
        raise ValueError(f"{label} parameters must be mappings")
    unknown = set(supplied) - set(declarations)
    if unknown:
        raise ValueError(f"Unknown {label} parameters: {sorted(unknown)}")
    result = {}
    for name, declaration in declarations.items():
        _keys(declaration, {"type", "default", "enum", "description"}, f"parameter {name}")
        if name in supplied:
            value = supplied[name]
        elif "default" in declaration:
            value = copy.deepcopy(declaration["default"])
        else:
            raise ValueError(f"Missing {label} parameter: {name}")
        kind = declaration.get("type")
        valid = (
            (kind == "string" and isinstance(value, str))
            or (kind == "boolean" and type(value) is bool)
            or (kind == "integer" and type(value) is int)
            or (kind == "enum" and isinstance(value, str) and value in declaration.get("enum", []))
        )
        if not valid:
            raise ValueError(f"Invalid {label} parameter {name}: expected {kind}")
        result[name] = value
    return result


def _resolve(value, scope):
    if isinstance(value, list):
        return [_resolve(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, scope) for key, item in value.items()}
    if not isinstance(value, str):
        return value

    def reference(match):
        key = match.group(1).strip()
        if key not in scope:
            raise ValueError(f"Unsupported or unknown reference: {key}")
        return scope[key]

    match = REFERENCE.fullmatch(value)
    if match:
        return copy.deepcopy(reference(match))

    def embedded(match):
        item = reference(match)
        if type(item) is bool:
            return "true" if item else "false"
        return str(item)

    result = REFERENCE.sub(embedded, value)
    if re.search(r"<<\s*(?:pipeline|parameters)\.", result):
        raise ValueError(f"Malformed parameter reference: {value}")
    return result


def _condition(value, scope):
    value = _resolve(value, scope)
    if type(value) is bool:
        return value
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError(f"Unsupported condition: {value!r}")
    operator, operands = next(iter(value.items()))
    if operator == "not":
        return not _condition(operands, scope)
    if operator in {"and", "or"}:
        if not isinstance(operands, list) or not operands:
            raise ValueError(f"{operator} condition needs a nonempty list")
        # Evaluate every operand so unsupported syntax never hides behind a branch.
        results = [_condition(item, scope) for item in operands]
        return all(results) if operator == "and" else any(results)
    if operator == "equal" and isinstance(operands, list) and len(operands) == 2:
        return type(operands[0]) is type(operands[1]) and operands[0] == operands[1]
    raise ValueError(f"Unsupported condition operator: {operator}")


def _filter(value, scope):
    if isinstance(value, str) and value in scope:
        value = scope[value]
    value = _resolve(value, scope)
    if type(value) is not bool:
        raise ValueError("Only boolean parameter expression job filters are supported")
    return value


def _steps(steps, commands, scope, stack=()):
    if not isinstance(steps, list):
        raise ValueError("Job or command steps must be a list")
    result = []
    for step in steps:
        if isinstance(step, str):
            name, arguments = step, {}
        elif isinstance(step, dict) and len(step) == 1:
            name, arguments = next(iter(step.items()))
        else:
            raise ValueError(f"Unsupported step: {step!r}")
        if name == "when":
            _keys(arguments, {"condition", "steps"}, "when step")
            expanded = _steps(arguments.get("steps"), commands, scope, stack)
            if _condition(arguments.get("condition"), scope):
                result.extend(expanded)
        elif name in commands:
            if name in stack:
                raise ValueError(f"Recursive command: {name}")
            definition = commands[name]
            _keys(definition, {"description", "parameters", "steps"}, f"command {name}")
            values = _parameters(definition.get("parameters", {}), _resolve(arguments, scope), f"command {name}")
            command_scope = {key: val for key, val in scope.items() if not key.startswith("parameters.")}
            command_scope.update({f"parameters.{key}": val for key, val in values.items()})
            result.extend(_steps(definition.get("steps"), commands, command_scope, (*stack, name)))
        elif name in NATIVE_STEPS:
            body = _resolve(arguments, scope)
            if name == "run" and isinstance(body, str):
                pass
            else:
                _keys(body, STEP_OPTIONS[name], f"{name} step")
            result.append(name if isinstance(step, str) else {name: body})
        else:
            raise ValueError(f"Unsupported step or unknown command: {name}")
    return result


def _pack(shared_root):
    directory = shared_root / "config/application"
    document = yaml.safe_load((directory / "@config.yml").read_text())
    _keys(document, {"version", "parameters"}, "application root")
    for section in ("jobs", "commands", "workflows"):
        document[section] = {
            path.stem: yaml.safe_load(path.read_text())
            for path in sorted((directory / section).glob("*.yml"))
        }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for relative in ("scripts/edge", "phpcs.sh"):
            tar.add(shared_root / relative, arcname=relative)
    bundle = base64.b64encode(gzip.compress(archive.getvalue(), mtime=0)).decode("ascii")
    document["commands"]["prepare_shared_tools"] = {"steps": [{"run": {
        "name": "Load shared scripts bundled during setup",
        "environment": {"CI_SHARED_BUNDLE": bundle},
        "command": (shared_root / "scripts/unpack-shared-tools.sh").read_text(),
    }}]}
    return document


def compile_application(shared_root: Path, parameters: dict, *, branch="local",
                        trigger_source="api", schedule_name="") -> dict:
    """Compose application config and return selected, executable workflows.

    ``config`` is the packed unresolved snapshot; each selected workflow contains
    ``jobs`` with invocation ``name``, original ``definition``, ``requires`` and
    expanded ``config``. All outputs are JSON-serializable.
    """
    shared_root = Path(shared_root).resolve()
    return _compile_document(_pack(shared_root), parameters,
                             {"shared_root": str(shared_root)}, branch=branch,
                             trigger_source=trigger_source, schedule_name=schedule_name)


def compile_config(config_path: Path, parameters: dict, *, branch="local",
                   trigger_source="api", schedule_name="") -> dict:
    """Expand an already composed application YAML file without external tools."""
    config_path = Path(config_path).resolve()
    try:
        document = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(f'Invalid YAML in composed config: {config_path}') from error
    return _compile_document(document, parameters, {"config_path": str(config_path)},
                             branch=branch, trigger_source=trigger_source,
                             schedule_name=schedule_name)


def _compile_document(document, parameters, source, *, branch, trigger_source,
                      schedule_name):
    _keys(document, {"version", "parameters", "jobs", "commands", "workflows"},
          "application root")
    document = copy.deepcopy(document)
    for section in ("parameters", "jobs", "commands", "workflows"):
        document.setdefault(section, {})
        if not isinstance(document[section], dict):
            raise ValueError(f"Application {section} must be a mapping")
    if document.get("version") != 2.1:
        raise ValueError("Only application configuration version 2.1 is supported")
    effective = _parameters(document.get("parameters", {}), parameters, "pipeline")
    scope = {f"pipeline.parameters.{key}": value for key, value in effective.items()}
    scope.update({"pipeline.git.branch": branch, "pipeline.trigger_source": trigger_source,
                  "pipeline.schedule.name": schedule_name})
    workflows = {}
    for workflow_name, workflow in document["workflows"].items():
        _keys(workflow, {"when", "jobs"}, f"workflow {workflow_name}")
        selected = _condition(workflow.get("when", True), scope)
        jobs, filtered = [], set()
        for invocation in workflow.get("jobs", []):
            if isinstance(invocation, str):
                definition_name, arguments = invocation, {}
            elif isinstance(invocation, dict) and len(invocation) == 1:
                definition_name, arguments = next(iter(invocation.items()))
                arguments = arguments or {}
            else:
                raise ValueError(f"Unsupported job invocation: {invocation!r}")
            if definition_name not in document["jobs"]:
                raise ValueError(f"Unknown job: {definition_name}")
            definition = document["jobs"][definition_name]
            _keys(definition, {"description", "parameters", "docker", "shell", "steps", "working_directory", "environment"}, f"job {definition_name}")
            if not isinstance(arguments, dict):
                raise ValueError("Job arguments must be a mapping")
            arguments = copy.deepcopy(arguments)
            name = arguments.pop("name", definition_name)
            requires = arguments.pop("requires", [])
            if not isinstance(name, str) or not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
                raise ValueError("Job name and dependencies must be strings")
            if "filters" in arguments and not _filter(arguments.pop("filters"), scope):
                filtered.add(name)
            values = _parameters(definition.get("parameters", {}), _resolve(arguments, scope), f"job {definition_name}")
            job_scope = {**scope, **{f"parameters.{key}": value for key, value in values.items()}}
            config = _resolve({key: value for key, value in definition.items() if key not in {"parameters", "steps"}}, job_scope)
            config["steps"] = _steps(definition.get("steps"), document["commands"], job_scope)
            if not isinstance(config.get("docker"), list) or len(config["docker"]) != 1:
                raise ValueError("Only single-container Docker jobs are supported")
            _keys(config["docker"][0], {"image", "environment", "user"}, "Docker executor")
            jobs.append({"name": name, "definition": definition_name, "requires": requires, "config": config})
        names = [job["name"] for job in jobs]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate job invocation names in {workflow_name}")
        visited, visiting = set(), set()

        def visit(name):
            if name in visiting:
                raise ValueError(f"Dependency cycle at job {name}")
            if name in visited:
                return
            if name not in names:
                raise ValueError(f"Unknown required job: {name}")
            visiting.add(name)
            job = jobs[names.index(name)]
            for dependency in job["requires"]:
                visit(dependency)
                if dependency in filtered:
                    filtered.add(name)
            visiting.remove(name)
            visited.add(name)

        for name in names:
            visit(name)
        if selected:
            workflows[workflow_name] = {"jobs": [job for job in jobs if job["name"] not in filtered]}
    return {"parameters": effective, "source": {**source,
            "branch": branch, "trigger_source": trigger_source, "schedule_name": schedule_name},
            "config": document, "workflows": workflows}
