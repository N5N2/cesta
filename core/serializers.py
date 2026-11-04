"""Serializers do app ``core``."""
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Delivery, Family, first_day_of_month, validate_cpf


def normalize_cpf(raw):
    """Remove pontuação do CPF, deixando apenas dígitos."""
    return re.sub(r"\D", "", raw or "")


class FamilySerializer(serializers.ModelSerializer):
    """Família. Entrada: ``cpf`` (com ou sem máscara). Saída: ``cpf_masked``."""

    responsible_name = serializers.CharField(source="full_name", max_length=160)
    cpf = serializers.CharField(write_only=True)
    cpf_masked = serializers.CharField(source="masked_cpf", read_only=True)
    delivered_this_month = serializers.SerializerMethodField()
    last_delivery_at = serializers.SerializerMethodField()

    class Meta:
        model = Family
        fields = [
            "id",
            "responsible_name",
            "cpf",
            "cpf_masked",
            "address",
            "neighborhood",
            "members_count",
            "is_active",
            "delivered_this_month",
            "last_delivery_at",
        ]

    def validate_cpf(self, value):
        """Normaliza, valida e garante unicidade do CPF (mensagem em JSON)."""
        cpf = normalize_cpf(value)
        try:
            validate_cpf(cpf)
        except DjangoValidationError:
            raise serializers.ValidationError("CPF inválido.")

        duplicates = Family.objects.filter(cpf=cpf)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Já existe uma família com este CPF.")
        return cpf

    def validate_members_count(self, value):
        if value < 1:
            raise serializers.ValidationError("Informe ao menos 1 membro.")
        return value

    # -- campos calculados (usam anotações da view quando disponíveis) --------
    def get_delivered_this_month(self, obj):
        annotated = getattr(obj, "delivered_this_month_annot", None)
        if annotated is not None:
            return annotated
        return obj.deliveries.filter(reference_month=first_day_of_month()).exists()

    def get_last_delivery_at(self, obj):
        annotated = getattr(obj, "last_delivery_at_annot", None)
        if annotated is not None or hasattr(obj, "last_delivery_at_annot"):
            return annotated
        last = obj.deliveries.order_by("-delivered_at").first()
        return last.delivered_at if last else None


class DeliveryCreateSerializer(serializers.Serializer):
    """Entrada de ``POST /api/deliveries/`` — apenas o ID da família."""

    family_id = serializers.IntegerField(min_value=1)


class DeliverySerializer(serializers.ModelSerializer):
    """Saída de entregas (criação e histórico)."""

    family_id = serializers.IntegerField(read_only=True)
    family_name = serializers.CharField(source="family.full_name", read_only=True)
    reference_month = serializers.CharField(source="reference_label", read_only=True)
    delivered_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Delivery
        fields = [
            "id",
            "family_id",
            "family_name",
            "reference_month",
            "delivered_at",
            "delivered_by",
        ]
        read_only_fields = fields
