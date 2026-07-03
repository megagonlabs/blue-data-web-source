class KeywordSearch:

    def search(
        self,
        records,
        query,
        scope=None,
        record_type=None,
        page=0,
        page_size=10,
    ):
        query = query.lower()

        results = []

        for record in records:

            if record_type:
                if record.get("type") != record_type:
                    continue

            if scope:
                if not record.get(
                    "scope",
                    "",
                ).startswith(scope):
                    continue

            score = 0

            name = record.get(
                "name",
                "",
            ).lower()

            description = record.get(
                "description",
                "",
            ).lower()

            if query in name:
                score += 10

            if query in description:
                score += 5

            if score > 0:
                results.append(
                    {
                        "record": record,
                        "score": score,
                    }
                )

        results.sort(
            key=lambda x: x["score"],
            reverse=True,
        )

        start = page * page_size
        end = start + page_size

        return results[start:end]