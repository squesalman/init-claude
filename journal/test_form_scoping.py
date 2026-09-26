"""
Tripwire for follow-ups row 5 (docs/data/follow-ups.md): UserOwned models set
Meta.default_manager_name = "unscoped" (Django needs a non-raising default for reverse
relations), and a ModelForm builds FK dropdowns from `_default_manager`, so a naive
ModelForm lists every tenant's rows. No forms exist yet; this fails the moment one is
added without inheriting a `UserScopedModelForm` base (to be built with the first form,
scoping every FK queryset via `for_user(user)`).
"""

import importlib.util
from importlib import import_module

from django import forms
from django.apps import apps

from journal.models import UserOwned


def _touches_user_owned(form_cls) -> bool:
    model = form_cls._meta.model
    if issubclass(model, UserOwned):
        return True
    return any(
        f.is_relation and f.related_model and issubclass(f.related_model, UserOwned)
        for f in model._meta.get_fields()
    )


def _unscoped_forms(form_classes):
    return [
        c
        for c in form_classes
        if _touches_user_owned(c) and not any(b.__name__ == "UserScopedModelForm" for b in c.__mro__)
    ]


def _project_model_forms():
    for cfg in apps.get_app_configs():
        if not cfg.name.startswith(("journal", "accounts")):
            continue
        mod_name = f"{cfg.name}.forms"
        if importlib.util.find_spec(mod_name) is None:
            continue
        for obj in vars(import_module(mod_name)).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, forms.ModelForm)
                and obj.__module__ == mod_name
                and getattr(obj, "_meta", None)
                and obj._meta.model
            ):
                yield obj


def test_every_project_model_form_on_user_owned_data_is_tenant_scoped():
    bad = _unscoped_forms(_project_model_forms())
    assert not bad, (
        f"{[c.__name__ for c in bad]} build FK dropdowns from the unscoped default manager "
        "and would list other tenants' rows. Inherit UserScopedModelForm (scope every "
        "ModelChoiceField queryset with for_user(user)); see docs/data/follow-ups.md row 5."
    )


def test_tripwire_flags_an_unscoped_form_and_accepts_a_scoped_one():
    class Naive(forms.ModelForm):
        class Meta:
            model = UserOwned.__subclasses__()[0]
            fields = "__all__"

    class UserScopedModelForm(forms.ModelForm):
        pass

    class Scoped(UserScopedModelForm):
        class Meta:
            model = Naive._meta.model
            fields = "__all__"

    assert _unscoped_forms([Naive, Scoped]) == [Naive]
