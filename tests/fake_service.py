"""Подделка клиента Google Sheets API: запоминает вызовы, ничего не отправляет."""


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeValues:
    def __init__(self, svc):
        self.svc = svc

    def get(self, spreadsheetId, range):
        self.svc.calls.append(("values.get", range))
        return _Req({"values": self.svc.values})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.svc.calls.append(("values.update", range, valueInputOption, body["values"]))
        return _Req({"updatedRange": range})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.svc.calls.append(("values.append", range, body["values"]))
        return _Req({})

    def clear(self, spreadsheetId, range):
        self.svc.calls.append(("values.clear", range))
        return _Req({})


class FakeSpreadsheets:
    def __init__(self, svc):
        self.svc = svc

    def get(self, spreadsheetId):
        return _Req({"properties": {"title": "Отчёты"},
                     "sheets": [{"properties": {"title": t, "sheetId": i}}
                                for t, i in self.svc.sheets.items()]})

    def values(self):
        return FakeValues(self.svc)

    def batchUpdate(self, spreadsheetId, body):
        self.svc.batches.append(body["requests"])
        replies = []
        for req in body["requests"]:
            if "addSheet" in req:
                title = req["addSheet"]["properties"]["title"]
                sid = 100 + len(self.svc.sheets)
                self.svc.sheets[title] = sid
                replies.append({"addSheet": {"properties": {"title": title, "sheetId": sid}}})
            else:
                replies.append({})
        return _Req({"replies": replies})


class FakeService:
    def __init__(self, values=None):
        self.sheets = {"Лист1": 0}
        self.values = values or []
        self.calls = []
        self.batches = []

    def spreadsheets(self):
        return FakeSpreadsheets(self)
