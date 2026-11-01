# Synthetic test data — not the Empório catalog

These CSVs are invented for automated tests. They are not the store's catalog, prices
or promotions, and contain no personal data. Columns mirror the challenge files so the
loader can be tested in a clean clone without the original sources.

They deliberately include ambiguous but loadable cases (active with zero stock,
discontinued with stock, coming soon with stock, an unused category, a product with two
promotion rows) so the quality warnings are exercised. Tests build broken copies in a
temporary folder; these files stay valid.
