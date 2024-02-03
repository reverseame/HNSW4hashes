
# User-defined exceptions
class ApotheosisUnmatchDistanceAlgorithmError(Exception):
    pass
class ApotheosisIsEmptyError(Exception):
    pass

# HNSW-related exceptions
class HNSWLayerDoesNotExistError(Exception):
    pass

class HNSWEmptyLayerError(Exception):
    pass

class HNSWIsEmptyError(Exception):
    pass

class HNSWUndefinedError(Exception):
    pass

class HNSWUnmatchDistanceAlgorithmError(Exception):
    pass

# node-related errors
class NodeLayerError(Exception):
    pass

class NodeNotFoundError(Exception):
    pass

class NodeAlreadyExistsError(Exception):
    pass
