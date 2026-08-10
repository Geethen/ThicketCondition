# Publication figure captions

## Reference-label selection

Each sampled location contributed one reference label. At all locations with two determinate labels (*n* = 92), one submitted source label was selected independently with equal probability using NumPy PCG64 (seed 0); unsure labels were ineligible, and locations with no determinate label (*n* = 25) were excluded.

**Figure 1 | Design-based area estimates for thicket condition and ecosystem type.**
**a,** Total mapped area in each reference-condition class; parenthetical values give the corresponding 95% confidence interval as a percentage of the mapped domain. **b,** Reference-condition area within Arid, Valley and Mesic thicket. Points show stratified design-based estimates and horizontal bars show 95% confidence intervals calculated following the Olofsson estimator. Reference observations labelled as no thicket were grouped with the severe class. The reference sample comprised 821 independently labelled points (Arid, *n* = 315; Valley, *n* = 385; Mesic, *n* = 121) across a mapped domain of 1,898,600 ha.

**Figure 2 | Design-based accuracy of the three-class thicket-condition map.**
Rows represent mapped classes and columns represent reference classes. Confusion-matrix cells and *n* values are unweighted reference-point counts; user's, producer's and overall accuracies are area-adjusted design-based estimates. Accuracy values are percentages, with 95% confidence intervals in parentheses. Reference observations labelled as no thicket were grouped with the severe class. Estimates use 821 independently labelled reference points.

**Figure 3 | Held-out evaluation of single- and multi-layer intact-class scores.**
**a,** Difference in held-out intact-class $F_1$ between each fixed multi-layer score and the $p_{intact}$ baseline. Points show observed differences and horizontal bars show percentile 95% confidence intervals from 10,000 paired bootstrap resamples of the 27 held-out 0.2° spatial blocks (*n* = 704 observations). The vertical line denotes no difference; all displayed intervals include zero. Rule thresholds were selected using the training folds only (*n* = 1,379). **b,** Held-out $F_1$ for every expression on the PySR training-loss/complexity front. Circles denote expressions containing $p_i$ only and triangles denote expressions that additionally contain $p_m$ or $p_s$; the horizontal line is the $p_i$ baseline. Here $p_i$, $p_m$ and $p_s$ are the intact, moderate and severe Random Forest probability layers. The bootstrap comparison is conditional on the cached five-fold out-of-fold probability predictions and is not a fully nested end-to-end validation of the complete model-selection pipeline.
