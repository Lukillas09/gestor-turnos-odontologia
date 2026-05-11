from django import forms

from turnos.models import Odontologo, validar_foto_odontologo


class PerfilUsuarioForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="Nombre")
    last_name = forms.CharField(max_length=150, required=False, label="Apellido")
    email = forms.EmailField(required=False, label="Email")
    especialidad = forms.CharField(max_length=100, required=False)
    matricula = forms.CharField(max_length=50, required=False)
    duracion_turno_minutos = forms.IntegerField(
        min_value=1,
        required=False,
        label="Duracion del turno",
    )
    foto_perfil = forms.FileField(
        required=False,
        label="Foto de perfil",
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    foto_posicion_x = forms.IntegerField(
        min_value=0,
        max_value=100,
        required=False,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100}),
    )
    foto_posicion_y = forms.IntegerField(
        min_value=0,
        max_value=100,
        required=False,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100}),
    )

    def __init__(self, *args, usuario=None, odontologo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.odontologo = odontologo

        if usuario and not self.is_bound:
            self.initial.update(
                {
                    "first_name": usuario.first_name,
                    "last_name": usuario.last_name,
                    "email": usuario.email,
                }
            )

        if odontologo and not self.is_bound:
            self.initial.update(
                {
                    "especialidad": odontologo.especialidad,
                    "matricula": odontologo.matricula,
                    "duracion_turno_minutos": odontologo.duracion_turno_minutos,
                    "foto_posicion_x": odontologo.foto_posicion_x,
                    "foto_posicion_y": odontologo.foto_posicion_y,
                }
            )

        if odontologo:
            self.fields["matricula"].required = True
            self.fields["duracion_turno_minutos"].required = True
        else:
            for field_name in (
                "especialidad",
                "matricula",
                "duracion_turno_minutos",
                "foto_perfil",
                "foto_posicion_x",
                "foto_posicion_y",
            ):
                self.fields.pop(field_name)

    def clean_matricula(self):
        if "matricula" not in self.fields:
            return ""

        matricula = self.cleaned_data["matricula"].strip()
        existentes = Odontologo.objects.filter(matricula=matricula)

        if self.odontologo and self.odontologo.pk:
            existentes = existentes.exclude(pk=self.odontologo.pk)

        if existentes.exists():
            raise forms.ValidationError("Ya existe un odontologo con esa matricula.")

        return matricula

    def clean_foto_perfil(self):
        foto = self.cleaned_data.get("foto_perfil")
        validar_foto_odontologo(foto)
        return foto

    def save(self):
        usuario = self.usuario
        usuario.first_name = self.cleaned_data["first_name"]
        usuario.last_name = self.cleaned_data["last_name"]
        usuario.email = self.cleaned_data["email"]
        usuario.save(update_fields=["first_name", "last_name", "email"])

        if not self.odontologo:
            return usuario

        odontologo = self.odontologo
        odontologo.especialidad = self.cleaned_data["especialidad"]
        odontologo.matricula = self.cleaned_data["matricula"]
        odontologo.duracion_turno_minutos = self.cleaned_data["duracion_turno_minutos"]
        odontologo.foto_posicion_x = self.cleaned_data.get("foto_posicion_x") or 50
        odontologo.foto_posicion_y = self.cleaned_data.get("foto_posicion_y") or 50

        foto_perfil = self.cleaned_data.get("foto_perfil")

        if foto_perfil:
            odontologo.foto_perfil = foto_perfil

        odontologo.save()
        return usuario
