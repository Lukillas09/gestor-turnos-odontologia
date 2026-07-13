class PublicShellMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["usar_shell_publico"] = True
        return context
