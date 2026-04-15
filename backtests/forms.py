from django import forms

from .strategies import STRATEGY_CHOICES

tw = "w-full px-3 py-2 border border-gray-600 bg-gray-800 text-gray-100 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"


class BacktestForm(forms.Form):
    symbol = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"class": tw, "placeholder": "AAPL"}),
    )
    strategy_name = forms.ChoiceField(
        choices=STRATEGY_CHOICES,
        widget=forms.Select(attrs={"class": tw}),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": tw}),
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": tw}),
    )
    initial_capital = forms.FloatField(
        initial=10000.0, min_value=100.0,
        widget=forms.NumberInput(attrs={"class": tw}),
    )

    # ORB strategy params
    opening_range_minutes = forms.IntegerField(
        initial=15, min_value=1, max_value=60, required=False,
        widget=forms.NumberInput(attrs={"class": tw}),
    )
    stop_loss_pct = forms.FloatField(
        initial=1.0, min_value=0.1, max_value=5.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.1"}),
    )
    take_profit_pct = forms.FloatField(
        initial=2.0, min_value=0.1, max_value=10.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.1"}),
    )
    use_atr_stops = forms.BooleanField(
        initial=True, required=False,
        widget=forms.CheckboxInput(attrs={"class": "rounded bg-gray-800 border-gray-600 text-blue-500"}),
    )
    atr_stop_mult = forms.FloatField(
        initial=1.5, min_value=0.5, max_value=4.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.5"}),
    )
    atr_tp_mult = forms.FloatField(
        initial=3.0, min_value=1.0, max_value=8.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.5"}),
    )
    entry_cutoff_minutes = forms.IntegerField(
        initial=180, min_value=30, max_value=300, required=False,
        widget=forms.NumberInput(attrs={"class": tw}),
    )
    volume_threshold = forms.FloatField(
        initial=1.0, min_value=0.0, max_value=5.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.1"}),
    )
    max_gap_pct = forms.FloatField(
        initial=3.0, min_value=0.5, max_value=10.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.5"}),
    )
    min_range_pct = forms.FloatField(
        initial=0.3, min_value=0.0, max_value=2.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.1"}),
    )
    max_range_pct = forms.FloatField(
        initial=3.0, min_value=1.0, max_value=10.0, required=False,
        widget=forms.NumberInput(attrs={"class": tw, "step": "0.5"}),
    )


class OptimizationForm(forms.Form):
    symbol = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"class": tw, "placeholder": "AAPL"}),
    )
    strategy_name = forms.ChoiceField(
        choices=STRATEGY_CHOICES,
        widget=forms.Select(attrs={"class": tw}),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": tw}),
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": tw}),
    )
    n_trials = forms.IntegerField(
        initial=50, min_value=10, max_value=500,
        widget=forms.NumberInput(attrs={"class": tw}),
    )
    objective_metric = forms.ChoiceField(
        choices=[
            ("sharpe_ratio", "Sharpe Ratio"),
            ("sortino_ratio", "Sortino Ratio"),
            ("total_return_pct", "Total Return %"),
            ("profit_factor", "Profit Factor"),
        ],
        widget=forms.Select(attrs={"class": tw}),
    )
