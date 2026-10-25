__version__ = "1.0.0"

"""userguide — compile the official WRF documentation (Registry / README.namelist / Users' Guide) into a visual
parameter form.

Pipeline (fully deterministic; changing the WRF version only touches sources.yaml)::

    fetch   upstream snapshot -> references/upstream/wrf-<ver>/ + MANIFEST.yaml
    parse   Registry rconfig + README.namelist + WPS all_options + Users' Guide anchors
            -> registry_fields.yaml (the full field set, machine-generated, never hand-written)
    render  registry_fields.yaml (+) field_map.yaml (the overlay annotated by a person or an agent)
            -> a self-contained single-file HTML form (design fields only; the experiment YAML is exported in the
               browser)

CLI: ``python -m src.userguide {fetch,parse,render,all}``
"""
