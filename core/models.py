"""Modelos do app ``core`` — Cesta Certa.

Entidades:
    * ``User``     -- voluntário (estende ``AbstractUser``; senha com PBKDF2).
    * ``Family``   -- família assistida (soft delete via ``is_active``).
    * ``Delivery`` -- entrega de cesta (1 por família por mês de referência).
"""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def first_day_of_month(value=None):
    """Retorna o primeiro dia do mês de ``value`` (padrão: mês corrente)."""
    value = value or timezone.localdate()
    return value.replace(day=1)


def validate_cpf(value):
    """Valida um CPF (somente dígitos) pelo algoritmo dos dígitos verificadores."""
    if len(value) != 11 or not value.isdigit() or value == value[0] * 11:
        raise ValidationError("CPF inválido.", code="invalid_cpf")

    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(value[:size], range(size + 1, 1, -1)))
        digit = (total * 10) % 11 % 10
        if digit != int(value[size]):
            raise ValidationError("CPF inválido.", code="invalid_cpf")


class User(AbstractUser):
    """Voluntário do sistema.

    A autenticação usa o hasher padrão do Django (PBKDF2-SHA256), que é o
    primeiro item de ``PASSWORD_HASHERS`` -- não o sobrescreva em settings.
    """

    class Meta:
        verbose_name = "voluntário"
        verbose_name_plural = "voluntários"

    def __str__(self):
        return self.get_full_name() or self.get_username()


class Family(models.Model):
    """Família assistida. Nunca é apagada: apenas inativada (soft delete)."""

    full_name = models.CharField("nome completo", max_length=160)
    # Armazenado somente com dígitos (11 caracteres); único no banco.
    cpf = models.CharField(
        "CPF", max_length=11, unique=True, validators=[validate_cpf]
    )
    address = models.CharField("endereço", max_length=240)
    neighborhood = models.CharField("bairro", max_length=100)
    members_count = models.PositiveSmallIntegerField("quantidade de membros")
    is_active = models.BooleanField("ativa", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "família"
        verbose_name_plural = "famílias"

    def __str__(self):
        return f"{self.full_name} ({self.masked_cpf})"

    @property
    def masked_cpf(self):
        """CPF mascarado para exibição (LGPD): ``***.742.***-**``."""
        return f"***.{self.cpf[3:6]}.***-**"


class Delivery(models.Model):
    """Registro de entrega de uma cesta básica a uma família."""

    # PROTECT: o histórico de entregas nunca some junto com a família/voluntário.
    family = models.ForeignKey(
        Family, on_delete=models.PROTECT, related_name="deliveries"
    )
    # Sempre o 1º dia do mês (ex.: 2026-10-01 representa "10/2026").
    reference_month = models.DateField("mês de referência")
    delivered_at = models.DateTimeField("data e hora do registro", auto_now_add=True)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deliveries",
    )

    class Meta:
        ordering = ["-delivered_at"]
        verbose_name = "entrega"
        verbose_name_plural = "entregas"
        constraints = [
            # Regra 1: fonte final de verdade, inclusive contra concorrência.
            models.UniqueConstraint(
                fields=["family", "reference_month"],
                name="unique_family_delivery_per_month",
            ),
        ]

    def __str__(self):
        return f"{self.family_id} — {self.reference_label}"

    def save(self, *args, **kwargs):
        # Normaliza para o 1º dia do mês, garantindo a unicidade mensal.
        self.reference_month = first_day_of_month(self.reference_month)
        super().save(*args, **kwargs)

    @property
    def reference_label(self):
        """Mês de referência no formato ``MM/AAAA`` (ex.: ``10/2026``)."""
        return self.reference_month.strftime("%m/%Y")
