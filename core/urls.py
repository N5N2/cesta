"""Rotas da API (incluir em ``config/urls.py`` com ``path("api/", include("core.urls"))``)."""
from rest_framework.routers import DefaultRouter

from .views import DeliveryViewSet, FamilyViewSet

router = DefaultRouter()
router.register("families", FamilyViewSet, basename="family")
router.register("deliveries", DeliveryViewSet, basename="delivery")

urlpatterns = router.urls
