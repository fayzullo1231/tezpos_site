from django import forms

from .models import Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "unit",
            "category",
            "brand",
            "selling_price",
            "wholesale_price",
            "cost_price",
            "stock_qty",
            "min_stock",
            "is_favorite",
            "image_url",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Mahsulot nomi"}),
            "category": forms.TextInput(attrs={"placeholder": "Bo‘lim"}),
            "brand": forms.Select(),
            "unit": forms.Select(),
            "is_favorite": forms.CheckboxInput(),
        }
        labels = {
            "name": "Nomi",
            "unit": "O‘lchov birligi",
            "category": "Bo‘lim",
            "brand": "Brend",
            "cost_price": "Sotib olish narxi",
            "stock_qty": "Omborda qoldiq",
            "selling_price": "Sotuv narxi",
            "wholesale_price": "Ulgurji narx",
            "min_stock": "Minimal qoldiqda ogohlantirish",
            "is_favorite": "Sevimlilar",
            "image_url": "Rasm URL",
        }

    def __init__(self, *args, brand_choices=None, category_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].required = False
        self.fields["brand"].widget = forms.TextInput()
        self.fields["category"].required = False
        self.fields["category"].widget = forms.TextInput()
        self.brand_choices = brand_choices or []
        self.category_choices = category_choices or []
        for name in ("cost_price", "stock_qty", "wholesale_price", "min_stock"):
            self.fields[name].required = False
