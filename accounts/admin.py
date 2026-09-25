from django.contrib import admin

# Register your models here.
#
# Warning (round-5 code review, nothing registered yet, nothing to fix — just flagging
# for whoever registers a UserOwned model here first): journal.UserOwned's
# Meta.default_manager_name = "unscoped" means Django admin's ModelAdmin.get_queryset()
# (which reads _default_manager) would show every tenant's rows the moment any
# UserOwned model is registered. Registering one requires an explicit get_queryset()
# override on that ModelAdmin, scoped to the current request's user — do not register a
# UserOwned model here without it. accounts.User itself isn't UserOwned (it's the
# tenant root), so this doesn't apply to a plain `admin.site.register(User)`.
