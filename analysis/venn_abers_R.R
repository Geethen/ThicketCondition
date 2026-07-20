## venn_abers_R.R
## ================
## Independent R port of the Venn-ABERS calibrator (ip200/venn-abers, binary
## classification setting) plus the same one-vs-rest / cross-VA multiclass wrapper
## and calibration metrics used by 20_venn_abers.py.
##
## It reads the raw probabilities, labels and FOLD ids that the Python script
## exported (_va_labelled_oof.csv) so both implementations use the identical OOF
## splits -- the comparison is then exact, isolating implementation differences
## rather than fold-shuffle differences.
##
## Outputs:
##   results/_va_labelled_oof_R.csv   R's OOF calibrated probs (matched to Python)
##   results/venn_abers_R.json        R metrics (uncalibrated vs VA)
##
## Run: & "C:/Program Files/R/R-4.5.0/bin/Rscript.exe" analysis/venn_abers_R.R

## base R only -- no external calibration package needed.

CLASSES <- c("intact", "moderate", "severe")
HERE <- tryCatch({
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f)) else getwd()
}, error = function(e) getwd())
RESULTS <- file.path(HERE, "results")

## ---------------------------------------------------------------- core: calc_p0p1
## Faithful port of the classification branch of calc_p0p1 (0-based Python indexing
## mapped to 1-based R). Returns list(p0, p1, c) where p0/p1 are the isotonic value
## vectors (length length(c)+1) and c the sorted unique calibration scores.
calc_p0p1 <- function(p_cal, y_cal) {
  ## p_cal: matrix n x 2 (col 2 = P(class==1)); y_cal: 0/1 vector
  score <- p_cal[, 2]
  ix <- order(score)
  k_sort <- score[ix]
  k_label_sort <- y_cal[ix]

  c_vals <- sort(unique(k_sort))
  ## ia (0-based first index of each unique value) == python np.searchsorted(k_sort,c)
  ia0 <- findInterval(c_vals - .Machine$double.eps, k_sort)  # count of elements < c
  ia0 <- vapply(c_vals, function(v) sum(k_sort < v), numeric(1))  # exact, 0-based

  kd <- length(c_vals)
  w <- numeric(kd)
  if (kd > 1) w[1:(kd - 1)] <- diff(ia0)
  w[kd] <- length(k_sort) - ia0[kd]

  csum <- cumsum(k_label_sort)
  ## P has kd+2 rows (python indices 0..kd+1); use 1-based rows 1..kd+2
  P <- matrix(0, nrow = kd + 2, ncol = 2)
  P[1, 1] <- -1
  P[3:(kd + 2), 1] <- cumsum(w)
  ## python: P[2:-1,1] = csum[(ia-1)[1:]]  -> rows 3..(kd+1), ia (0-based) [2..kd]
  if (kd >= 2) {
    idx <- ia0[2:kd]            # 0-based indices -> +1 for R
    P[3:(kd + 1), 2] <- csum[idx]  # csum[(ia-1)+1] with ia0 0-based == csum[ia0]
  }
  P[kd + 2, 2] <- csum[length(csum)]

  m <- length(c_vals) + 1        # length of p1/p0 value arrays

  ## ---- p1: GCM from the left. P1 = P[1:] (python) = P rows 2..(kd+2) here ----
  P1 <- P[2:(kd + 2), , drop = FALSE] + 1
  p1v <- numeric(m)
  grad <- NA_real_
  cpt <- 1
  for (i in 1:m) {
    P1[i, ] <- P1[i, ] - 1
    if (i == 1) {
      grads <- P1[, 2] / P1[, 1]
      grad <- min(grads, na.rm = TRUE)
      p1v[i] <- grad
      cpt <- 1
    } else {
      imp <- P1[cpt, 2] + (P1[i, 1] - P1[cpt, 1]) * grad
      if (P1[i, 2] < imp) {
        g <- (P1[i:m, 2] - P1[i, 2]) / (P1[i:m, 1] - P1[i, 1])
        if (!all(is.na(g))) grad <- min(g, na.rm = TRUE)
        cpt <- i
        p1v[i] <- grad
      } else {
        p1v[i] <- grad
      }
    }
  }

  ## ---- p0: LCM from the right. P0 = P[1:] (python) = P rows 2..(kd+2) ----
  P0 <- P[2:(kd + 2), , drop = FALSE]
  p0v <- numeric(m)
  cpt <- m
  for (i in m:1) {
    P0[i, 1] <- P0[i, 1] + 1
    if (i == m) {
      g <- (P0[, 2] - P0[i, 2]) / (P0[, 1] - P0[i, 1])
      grad <- max(g, na.rm = TRUE)
      p0v[i] <- grad
      cpt <- i
    } else {
      imp <- P0[cpt, 2] + (P0[i, 1] - P0[cpt, 1]) * grad
      if (P0[i, 2] < imp) {
        g <- (P0[, 2] - P0[i, 2]) / (P0[, 1] - P0[i, 1])
        g[i:m] <- 0
        grad <- max(g, na.rm = TRUE)
        cpt <- i
        p0v[i] <- grad
      } else {
        p0v[i] <- grad
      }
    }
  }

  list(p0 = p0v, p1 = p1v, c = c_vals)
}

## ---------------------------------------------------------------- core: calc_probs
## p0/p1 indexed by python np.searchsorted(c, out, side). Reproduce both sides.
searchsorted <- function(a, v, side = "left") {
  ## number of elements in sorted a that are strictly-less-than (left) or
  ## less-than-or-equal (right) v -> 0-based insertion index (like numpy).
  if (side == "left") {
    vapply(v, function(x) sum(a < x), numeric(1))
  } else {
    vapply(v, function(x) sum(a <= x), numeric(1))
  }
}

calc_probs <- function(fit, p_test) {
  out <- p_test[, 2]
  ## python p0[searchsorted(c,out,'right')] , p1[searchsorted(c,out,'left')]
  i0 <- searchsorted(fit$c, out, "right")   # 0-based
  i1 <- searchsorted(fit$c, out, "left")
  p0sel <- fit$p0[i0 + 1]                    # +1 -> 1-based R
  p1sel <- fit$p1[i1 + 1]
  p_prime1 <- p1sel / (1 - p0sel + p1sel)
  cbind(1 - p_prime1, p_prime1)
}

va_fit <- function(p_cal, y_cal) calc_p0p1(p_cal, y_cal)

## ---------------------------------------------------------------- cross-VA (OOF)
cross_va_oof <- function(P, y, fold) {
  n <- nrow(P)
  oof <- matrix(0, n, 3)
  for (k in sort(unique(fold))) {
    te <- which(fold == k)
    tr <- which(fold != k)
    for (ci in 1:3) {
      score2_tr <- cbind(1 - P[tr, ci], P[tr, ci])
      yb_tr <- as.integer(y[tr] == (ci - 1))
      fit <- va_fit(score2_tr, yb_tr)
      score2_te <- cbind(1 - P[te, ci], P[te, ci])
      pp <- calc_probs(fit, score2_te)
      oof[te, ci] <- pp[, 2]
    }
  }
  s <- rowSums(oof); s[s == 0] <- 1
  oof / s
}

## ---------------------------------------------------------------- metrics
onehot <- function(y, k = 3) { m <- matrix(0, length(y), k); m[cbind(seq_along(y), y + 1)] <- 1; m }
brier <- function(P, y) mean(rowSums((P - onehot(y))^2))
logloss <- function(P, y, eps = 1e-12) {
  Pc <- pmin(pmax(P, eps), 1)
  -mean(log(Pc[cbind(seq_along(y), y + 1)]))
}
ece_bin <- function(conf, correct, nb = 10) {
  edges <- seq(0, 1, length.out = nb + 1); e <- 0; n <- length(conf)
  for (b in 1:nb) {
    lo <- edges[b]; hi <- edges[b + 1]
    m <- if (b == 1) (conf >= lo & conf <= hi) else (conf > lo & conf <= hi)
    if (any(m)) e <- e + sum(m) / n * abs(mean(correct[m]) - mean(conf[m]))
  }
  e
}
classwise_ece <- function(P, y, nb = 10) {
  v <- sapply(1:3, function(ci) ece_bin(P[, ci], as.numeric(y == (ci - 1)), nb))
  names(v) <- CLASSES; c(as.list(v), mean = mean(v))
}
metrics_block <- function(P, y) {
  top <- max.col(P, ties.method = "first") - 1
  conf <- P[cbind(seq_along(y), top + 1)]
  correct <- as.numeric(top == y)
  list(brier = brier(P, y), logloss = logloss(P, y),
       ece_toplabel = ece_bin(conf, correct),
       ece_classwise = classwise_ece(P, y),
       accuracy = mean(correct))
}

## ---------------------------------------------------------------- driver
d <- read.csv(file.path(RESULTS, "_va_labelled_oof.csv"))
P <- as.matrix(d[, c("raw_intact", "raw_moderate", "raw_severe")])
P <- P / rowSums(P)
y <- d$y
fold <- d$fold

va_oof <- cross_va_oof(P, y, fold)

out_csv <- data.frame(id = d$id, y = y, fold = fold,
                      va_intact = va_oof[, 1], va_moderate = va_oof[, 2],
                      va_severe = va_oof[, 3])
write.csv(out_csv, file.path(RESULTS, "_va_labelled_oof_R.csv"), row.names = FALSE)

u <- metrics_block(P, y)
v <- metrics_block(va_oof, y)

## minimal JSON writer (avoid a jsonlite dependency)
num <- function(x) formatC(x, digits = 10, format = "g")
mb_json <- function(m) sprintf(
  '{"brier":%s,"logloss":%s,"ece_toplabel":%s,"ece_classwise":{"intact":%s,"moderate":%s,"severe":%s,"mean":%s},"accuracy":%s}',
  num(m$brier), num(m$logloss), num(m$ece_toplabel),
  num(m$ece_classwise$intact), num(m$ece_classwise$moderate),
  num(m$ece_classwise$severe), num(m$ece_classwise$mean), num(m$accuracy))
js <- sprintf('{"method":"venn_abers_cross_ovr_R","n_folds":%d,"n_points":%d,"uncalibrated":%s,"venn_abers_oof":%s}',
              length(unique(fold)), length(y), mb_json(u), mb_json(v))
writeLines(js, file.path(RESULTS, "venn_abers_R.json"))

cat(sprintf("R Venn-ABERS (n=%d)\n", length(y)))
cat(sprintf("%-28s%12s%14s%12s\n", "metric", "raw", "venn-abers", "delta"))
rows <- list(
  c("Brier", u$brier, v$brier),
  c("Log-loss", u$logloss, v$logloss),
  c("ECE top-label", u$ece_toplabel, v$ece_toplabel),
  c("ECE class-wise mean", u$ece_classwise$mean, v$ece_classwise$mean),
  c("Accuracy", u$accuracy, v$accuracy))
for (r in rows) cat(sprintf("%-28s%12.5f%14.5f%12.5f\n",
                            r[1], as.numeric(r[2]), as.numeric(r[3]),
                            as.numeric(r[3]) - as.numeric(r[2])))
cat("wrote", file.path(RESULTS, "venn_abers_R.json"), "\n")
