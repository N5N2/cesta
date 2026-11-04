"""Views REST do app ``core``.

Todas as rotas exigem autenticação (``IsAuthenticated``). Todo acesso ao banco
usa o ORM do Django (consultas parametrizadas -> sem SQL Injection).
"""
import re

from django.db import IntegrityError, transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Delivery, Family, first_day_of_month
from .serializers import (
    DeliveryCreateSerializer,
    DeliverySerializer,
    FamilySerializer,
)


class FamilyViewSet(viewsets.ModelViewSet):
    """Lista/cria/consulta/atualiza famílias; ``DELETE`` apenas inativa."""

    serializer_class = FamilySerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """Inclui inativas (busca/histórico) e anota o status do mês."""
        this_month = Delivery.objects.filter(
            family=OuterRef("pk"), reference_month=first_day_of_month()
        )
        queryset = Family.objects.annotate(
            delivered_this_month_annot=Exists(this_month),
            last_delivery_at_annot=Max("deliveries__delivered_at"),
        )

        term = self.request.query_params.get("search", "").strip()
        if term:
            digits = re.sub(r"\D", "", term)
            query = Q(full_name__icontains=term)
            if digits:
                query |= Q(cpf__contains=digits)
                query |= Q(pk=int(digits)) if len(digits) < 10 else Q()
            queryset = queryset.filter(query)
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Soft delete: marca ``is_active=False`` e preserva o histórico."""
        family = self.get_object()
        family.is_active = False
        family.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeliveryViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet
):
    """Registra entregas (``POST``) e lista o histórico (``GET ?month=``)."""

    serializer_class = DeliverySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Delivery.objects.select_related("family", "delivered_by")
        month = self.request.query_params.get("month", "")
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            year, mon = month.split("-")
            queryset = queryset.filter(
                reference_month__year=int(year), reference_month__month=int(mon)
            )
        return queryset.order_by("-delivered_at")

    def create(self, request, *args, **kwargs):
        """Registra a entrega do mês corrente aplicando as regras de negócio."""
        payload = DeliveryCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        # 404 se a família não existir.
        family = get_object_or_404(Family, pk=payload.validated_data["family_id"])

        # Regra 2: família inativa não recebe cesta.
        if not family.is_active:
            return Response(
                {
                    "code": "inactive_family",
                    "detail": "Família inativa: não é possível registrar entrega.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        month = first_day_of_month()

        # Regra 1: a UniqueConstraint é a garantia final (inclusive contra
        # dois voluntários confirmando ao mesmo tempo). O ``atomic`` isola a
        # falha para que a conexão siga utilizável após o IntegrityError.
        try:
            with transaction.atomic():
                delivery = Delivery.objects.create(
                    family=family, reference_month=month, delivered_by=request.user
                )
        except IntegrityError:
            existing = (
                Delivery.objects.select_related("delivered_by")
                .filter(family=family, reference_month=month)
                .first()
            )
            body = {
                "code": "duplicate_monthly_delivery",
                "detail": "Esta família já recebeu uma cesta neste mês.",
            }
            if existing:
                body["delivery"] = {
                    "delivered_at": existing.delivered_at,
                    "delivered_by": str(existing.delivered_by),
                }
            return Response(body, status=status.HTTP_409_CONFLICT)

        return Response(
            DeliverySerializer(delivery).data, status=status.HTTP_201_CREATED
        )
