# Measuring accuracy on real receipts

The app's tests use fictional data. Accuracy on real receipts is only known once you measure it here.

1. Put real receipts in `eval/real/` (photos, PDFs, saved emails). Aim for 30 or more, from at least 3 stores,
   including faded, long, discounted and weighed-item receipts.
2. For each one, write a label file with the same name and `.json`, **typed by a person from the receipt itself**,
   not copied from PriceWise. Format: see `eval/sample/*.json`.
3. Remove card numbers, names and loyalty numbers first. `eval/real/` is excluded from Git so it stays private.
4. Run `python evaluate.py eval/real` (add `--engine gemini` to test Gemini on photos).

It reports, per receipt and overall: lines read correctly, extra or wrong lines, whether store, date and total
were right, whether the lines reconcile to the subtotal, and seconds per receipt. Publish these numbers, failures
included.

`eval/sample/` holds the three fictional samples. They score 100%, which only shows the tool works.
