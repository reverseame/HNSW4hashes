from datalayer.node.node_hash import HashNode
from datalayer.hash_algorithm.hash_algorithm import HashAlgorithm

class WinModuleHashNode(HashNode):
    def __init__(self, id, hash_algorithm: HashAlgorithm, module):
        super().__init__(id, hash_algorithm)
        self._module = module
    
    def __lt__(self, other): # Hack for priority queue. TODO: not needed here?
        return False
