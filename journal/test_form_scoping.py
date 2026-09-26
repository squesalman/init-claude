"""
Tripwire for follow-ups row 5 (docs/data/follow-ups.md) and ADR-0006 Decision 2.
UserOwned models set Meta.default_manager_name = "unscoped" (Django needs a non-raising
default for reverse relations), and a ModelForm builds FK dropdowns from
`_default_manager`, so a naive ModelForm lists every tenant's rows. Every project
ModelForm on user-owned data must subclass `journal.forms.UserScopedModelForm` and never
expose `user` as a field. A grep test bans the unscoped query patterns in app code.
"""

import io
import pkgutil
import re
import tokenize
from importlib import import_module
from pathlib import Path

import pytest
from django import forms
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from journal.forms import UserScopedModelForm
from journal.models import ImportBatch, RawImportRow, UserOwned


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
        if _touches_user_owned(c) and not issubclass(c, UserScopedModelForm)
    ]


# First-party packages: the tripwire imports all their modules and the grep test scans them.
_PACKAGES = ("accounts", "journal", "config")


def _is_test_or_migration(module: str) -> bool:
    leaf = module.rsplit(".", 1)[-1]
    return leaf == "tests" or leaf.startswith("test_") or ".migrations" in module


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def _project_model_forms():
    # Import every first-party module so a ModelForm defined anywhere (views, services...)
    # exists as a subclass, then walk ModelForm's subclass tree.
    for pkg in _PACKAGES:
        for mod in pkgutil.walk_packages(import_module(pkg).__path__, f"{pkg}."):
            if not _is_test_or_migration(mod.name):
                import_module(mod.name)
    for cls in set(_all_subclasses(forms.ModelForm)):
        mod = cls.__module__
        if mod.split(".")[0] in _PACKAGES and not _is_test_or_migration(mod) and cls._meta.model:
            yield cls


def test_every_project_model_form_on_user_owned_data_is_tenant_scoped():
    project_forms = list(_project_model_forms())
    assert all("user" not in c.base_fields for c in project_forms), (
        "`user` must never be a form field (ADR-0006): the base form sets it from the request."
    )
    bad = _unscoped_forms(project_forms)
    assert not bad, (
        f"{[c.__name__ for c in bad]} build FK dropdowns from the unscoped default manager "
        "and would list other tenants' rows. Inherit UserScopedModelForm (scope every "
        "ModelChoiceField queryset with for_user(user)); see docs/data/follow-ups.md row 5."
    )


def test_tripwire_flags_an_unscoped_form_and_accepts_a_scoped_one():
    class Naive(forms.ModelForm):
        class Meta:
            model = RawImportRow
            fields = ["import_batch"]

    class LookAlike(forms.ModelForm):  # a local class of the same name must not pass
        class Meta:
            model = RawImportRow
            fields = ["import_batch"]

    LookAlike.__name__ = "UserScopedModelForm"

    class Scoped(UserScopedModelForm):
        class Meta:
            model = RawImportRow
            fields = ["import_batch"]

    assert _unscoped_forms([Naive, LookAlike, Scoped]) == [Naive, LookAlike]


# --- UserScopedModelForm behaviour (ADR-0006 Decision 2) --------------------------------


class _RowForm(UserScopedModelForm):
    class Meta:
        model = RawImportRow
        fields = ["import_batch", "line_number", "status"]


def _batch(user):
    return ImportBatch.objects.create(
        user=user, broker="topstep", filename="f.csv", file_sha256="0" * 64, raw_file=b""
    )


@pytest.fixture
def two_users(db):
    User = get_user_model()
    return (
        User.objects.create_user(email="a@example.com", password="x"),
        User.objects.create_user(email="b@example.com", password="x"),
    )


def test_user_scoped_form_sets_instance_user_and_never_exposes_it(two_users):
    a, _ = two_users
    form = _RowForm(user=a)
    assert form.instance.user == a
    assert "user" not in form.fields


def test_user_scoped_form_limits_choices_to_own_rows_and_rejects_foreign_ids(two_users):
    a, b = two_users
    mine, theirs = _batch(a), _batch(b)

    form = _RowForm(user=a)
    assert list(form.fields["import_batch"].queryset) == [mine]

    data = {"import_batch": theirs.pk, "line_number": 1, "status": RawImportRow.STATUS_IMPORTED}
    bad = _RowForm(data, user=a)
    assert not bad.is_valid()
    assert "import_batch" in bad.errors

    good = _RowForm(
        {**data, "import_batch": mine.pk}, instance=RawImportRow(raw={}), user=a
    )
    assert good.is_valid(), good.errors
    assert good.save().user == a


def test_user_scoped_form_rejects_an_instance_owned_by_another_user(two_users):
    a, b = two_users
    row = RawImportRow.objects.create(
        user=b, import_batch=_batch(b), line_number=1, raw={}, status=RawImportRow.STATUS_IMPORTED
    )
    with pytest.raises(PermissionDenied):
        _RowForm(instance=row, user=a)


# --- Banned-pattern grep test (ADR-0006 Decision 2, rule 3) -----------------------------

_ROOT = Path(__file__).resolve().parent.parent
_BANNED = {
    "_default_manager": re.compile(r"_default_manager"),
    ".unscoped": re.compile(r"\.unscoped\b"),
    "get_object_or_404(Model, ...)": re.compile(
        r"get_(?:object|list)_or_404\(\s*(?:\w+\.)*[A-Z]\w*\s*[,)]"
    ),
    "generic model view": re.compile(r"\b(?:List|Detail|Create|Update|Delete)View\b"),
    "bulk_create/bulk_update": re.compile(r"\bbulk_(?:create|update)\b"),
}


def _violations(source: str) -> list[str]:
    # Comments are dropped (via tokenize, so a "#" inside a string does not hide the rest
    # of the line) and prose about a banned pattern does not trip the test. Strings and
    # docstrings are still scanned.
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    code = tokenize.untokenize(t for t in tokens if t.type != tokenize.COMMENT)
    return [name for name, rx in _BANNED.items() if rx.search(code)]


def _app_files():
    for app in _PACKAGES:
        for path in (_ROOT / app).rglob("*.py"):
            rel = path.relative_to(_ROOT)
            if (
                "migrations" in rel.parts
                or path.name in {"tests.py", "admin.py"}
                or path.name.startswith("test_")
                or rel == Path("journal/models.py")
            ):
                continue
            yield rel, path


def test_app_code_uses_none_of_the_banned_unscoped_patterns():
    found = {str(rel): v for rel, path in _app_files() if (v := _violations(path.read_text()))}
    assert not found, (
        f"Banned by ADR-0006 Decision 2: {found}. Use Model.objects.for_user(request.user), "
        "function views and UserScopedModelForm. To add an exception, edit this test's "
        "exclusion list so it shows in PR review."
    )


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ("Foo._default_manager.all()", ["_default_manager"]),
        ("Foo.unscoped.filter(pk=1)", [".unscoped"]),
        ("get_object_or_404(Foo, pk=1)", ["get_object_or_404(Model, ...)"]),
        ("get_list_or_404(app.Foo)", ["get_object_or_404(Model, ...)"]),
        ("class V(ListView): pass", ["generic model view"]),
        ("Foo.objects.bulk_create(rows)", ["bulk_create/bulk_update"]),
        ("get_object_or_404(Foo.objects.for_user(u), pk=1)", []),
        ("# Foo.unscoped and bulk_create are banned", []),
        ('x = "/a#b"; Foo._default_manager', ["_default_manager"]),
    ],
)
def test_grep_scanner_flags_banned_patterns(snippet, expected):
    assert _violations(snippet) == expected


def test_tripwire_finds_a_model_form_defined_outside_a_forms_module():
    import gc

    class Stray(forms.ModelForm):
        class Meta:
            model = RawImportRow
            fields = ["import_batch"]

    Stray.__module__ = "journal.views"  # as if a view module defined it
    try:
        assert Stray in _unscoped_forms(_project_model_forms())
    finally:
        del Stray
        gc.collect()  # drop it from ModelForm.__subclasses__() for the real tripwire


def test_user_scoped_form_narrows_a_user_choice_field_to_the_current_user(two_users):
    a, b = two_users

    class WithUserChoice(_RowForm):
        reviewer = forms.ModelChoiceField(queryset=get_user_model().objects.all())

    form = WithUserChoice(user=a)
    assert list(form.fields["reviewer"].queryset) == [a]


def test_user_scoped_form_keeps_a_pre_narrowed_queryset(two_users):
    a, b = two_users
    shown, hidden, theirs = _batch(a), _batch(a), _batch(b)

    class Narrowed(_RowForm):
        import_batch = forms.ModelChoiceField(
            queryset=ImportBatch.unscoped.exclude(pk=hidden.pk)
        )

    # Neither the narrowed-out own row nor the other tenant's row is offered.
    assert list(Narrowed(user=a).fields["import_batch"].queryset) == [shown]
    assert theirs.user == b
