from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from journal.models import UserOwned


class UserScopedModelForm(forms.ModelForm):
    """
    Base for every ModelForm on a UserOwned model (ADR-0006 Decision 2). Build it as
    `Form(..., user=request.user)`. `user` must never be in Meta.fields: it is set here.
    Every FK dropdown is narrowed to the user's rows (as for_user() does, on top of any
    existing filter), and any User dropdown to the current user, so another tenant's id
    is an ordinary "invalid choice" instead of a cross-tenant write.
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id is None:
            self.instance.user = user
        elif self.instance.user_id != user.pk:
            raise PermissionDenied  # instance was not fetched via for_user()
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)
            if qs is not None and issubclass(qs.model, UserOwned):
                field.queryset = qs.filter(user=user)  # = for_user(), keeps any narrower filter
            elif qs is not None and issubclass(qs.model, get_user_model()):
                field.queryset = qs.filter(pk=user.pk)
