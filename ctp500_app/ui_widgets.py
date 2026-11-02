"""Reusable Tk dialogs for selecting existing records and editing structured fields.

These helpers keep domain models out of the widgets and never persist data.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Iterable, Sequence


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    kind: str = "text"  # text, int, optional_int, float, optional_float, choice, bool
    value: Any = ""
    options: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


def parse_field_value(field: FormField, raw: Any) -> Any:
    """Convert and validate form inputs without depending on a Tk display."""
    if field.kind == "bool":
        return bool(raw)
    value = str(raw).strip()
    if field.kind == "choice":
        if value not in field.options:
            raise ValueError(f"{field.label}: choose a listed value.")
        return value
    if field.kind in {"int", "optional_int", "float", "optional_float"}:
        if not value and field.kind.startswith("optional_"):
            return None
        try:
            number = int(value) if field.kind.endswith("int") else float(value)
        except ValueError as exc:
            raise ValueError(f"{field.label}: enter a valid number.") from exc
        if field.minimum is not None and number < field.minimum:
            raise ValueError(f"{field.label}: minimum is {field.minimum:g}.")
        if field.maximum is not None and number > field.maximum:
            raise ValueError(f"{field.label}: maximum is {field.maximum:g}.")
        return number
    return value


def prompt_form(parent: tk.Misc, title: str, fields: Sequence[FormField],
                *, submit_label: str = "Save") -> dict[str, Any] | None:
    """Edit related fields in one modal instead of a sequence of prompts."""
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.resizable(True, False)
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    variables: dict[str, tk.Variable] = {}
    first_input: tk.Widget | None = None
    for row, field in enumerate(fields):
        variable: tk.Variable = tk.BooleanVar(value=bool(field.value)) if field.kind == "bool" else tk.StringVar(value="" if field.value is None else str(field.value))
        variables[field.key] = variable
        if field.kind == "bool":
            widget = ttk.Checkbutton(body, text=field.label, variable=variable)
            widget.grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        else:
            ttk.Label(body, text=field.label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            if field.kind == "choice":
                widget = ttk.Combobox(body, values=field.options, textvariable=variable, state="readonly", width=33)
            elif field.kind in {"int", "optional_int", "float", "optional_float"}:
                widget = ttk.Entry(body, textvariable=variable, width=34)
            else:
                widget = ttk.Entry(body, textvariable=variable, width=34)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            if first_input is None:
                first_input = widget
    error_var = tk.StringVar()
    ttk.Label(body, textvariable=error_var, foreground="#b42318", wraplength=430).grid(
        row=len(fields), column=0, columnspan=2, sticky="w", pady=(5, 0))
    result: dict[str, Any] | None = None

    def submit(_event=None) -> None:
        nonlocal result
        try:
            result = {field.key: parse_field_value(field, variables[field.key].get()) for field in fields}
        except ValueError as exc:
            error_var.set(str(exc))
            return
        window.destroy()

    footer = ttk.Frame(body)
    footer.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
    ttk.Button(footer, text="Cancel", command=window.destroy).pack(side="left", padx=4)
    ttk.Button(footer, text=submit_label, command=submit).pack(side="left")
    window.bind("<Return>", submit)
    window.bind("<Escape>", lambda _event: window.destroy())
    window.grab_set()
    if first_input is not None:
        first_input.focus_set()
    parent.wait_window(window)
    return result


def choose_records(parent: tk.Misc, title: str, records: Iterable[Any],
                   columns: Sequence[tuple[str, Callable[[Any], Any], int]],
                   *, multiple: bool = False, submit_label: str = "Select",
                   initial_search: str = "") -> list[Any]:
    """Search and select saved records by their visible names, never by numeric index."""
    items = list(records)
    if not items:
        return []
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.geometry("690x490")
    body = ttk.Frame(window, padding=10)
    body.pack(fill="both", expand=True)
    query = tk.StringVar(value=initial_search)
    ttk.Label(body, text="Search").pack(anchor="w")
    search = ttk.Entry(body, textvariable=query)
    search.pack(fill="x", pady=(2, 6))
    names = tuple(f"col{index}" for index in range(len(columns)))
    tree = ttk.Treeview(body, columns=names, show="headings",
                        selectmode="extended" if multiple else "browse")
    for index, (name, _getter, width) in enumerate(columns):
        key = names[index]
        tree.heading(key, text=name)
        tree.column(key, width=width, stretch=index == 0)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
    scrollbar.pack(side="right", fill="y")
    tree.configure(yscrollcommand=scrollbar.set)
    by_key = {str(index): item for index, item in enumerate(items)}
    result: list[Any] = []

    def refresh(*_args) -> None:
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        needle = query.get().strip().casefold()
        for key, item in by_key.items():
            values = tuple(str(getter(item)) for _name, getter, _width in columns)
            if needle and needle not in " ".join(values).casefold():
                continue
            tree.insert("", "end", iid=key, values=values)
            if key in selected:
                tree.selection_add(key)

    def choose(_event=None) -> None:
        result.extend(by_key[key] for key in tree.selection() if key in by_key)
        if result:
            window.destroy()

    footer = ttk.Frame(window, padding=(10, 6, 10, 10))
    footer.pack(fill="x", side="bottom")
    ttk.Button(footer, text="Cancel", command=window.destroy).pack(side="right")
    ttk.Button(footer, text=submit_label, command=choose).pack(side="right", padx=5)
    tree.bind("<Double-1>", choose)
    window.bind("<Escape>", lambda _event: window.destroy())
    query.trace_add("write", refresh)
    refresh()
    search.focus_set()
    window.grab_set()
    parent.wait_window(window)
    return result
