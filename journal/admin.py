# Register your models here.
#
# Warning (round-5 code review, nothing registered yet, nothing to fix — just flagging
# for whoever registers one of these first): every model here (ImportBatch,
# RawImportRow, Execution, JournalEntry) is a UserOwned subclass with
# Meta.default_manager_name = "unscoped". Django admin's ModelAdmin.get_queryset()
# reads _default_manager, so registering any of them with a plain
# `admin.site.register(Model)` would show every tenant's rows in the admin the moment
# it's registered. Registering one requires an explicit get_queryset() override on that
# ModelAdmin, scoped to the current request's user (or restrict admin access to
# superusers who are expected to see everything — a deliberate call, not a default).
