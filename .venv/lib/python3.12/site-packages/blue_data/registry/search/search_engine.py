from abc import ABC, abstractmethod


class SearchEngine(ABC):

    @abstractmethod
    def keyword_search(self, *args, **kwargs):
        pass

    @abstractmethod
    def vector_search(self, *args, **kwargs):
        pass

    @abstractmethod
    def hybrid_search(self, *args, **kwargs):
        pass

    @abstractmethod
    def search_records_hierarchical(self, *args, **kwargs):
        pass