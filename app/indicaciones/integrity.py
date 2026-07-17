from historias.integrity import calcular_sello_integridad, sellos_coinciden


def crear_referencia_integridad(*, indicacion_uuid, snapshots):
    return calcular_sello_integridad(
        {
            "tipo": "indicacion_postoperatoria_referencia",
            "uuid": str(indicacion_uuid),
            "snapshots": snapshots,
        }
    )[:12]


def crear_sello_indicacion(*, indicacion_uuid, snapshots, pdf_sha256):
    return calcular_sello_integridad(
        {
            "tipo": "indicacion_postoperatoria",
            "uuid": str(indicacion_uuid),
            "snapshots": snapshots,
            "pdf_sha256": pdf_sha256,
        }
    )


def verificar_sello_indicacion(indicacion):
    esperado = crear_sello_indicacion(
        indicacion_uuid=indicacion.uuid,
        snapshots={
            "paciente": indicacion.snapshot_paciente,
            "profesional": indicacion.snapshot_profesional,
            "consultorio": indicacion.snapshot_consultorio,
            "documento": indicacion.snapshot_documento,
        },
        pdf_sha256=indicacion.pdf_sha256,
    )
    return sellos_coinciden(esperado, indicacion.sello_integridad)
