Cross Pod API Design (Conduit)
==============================

# Introduction

This document described the key design features of the cross-pod API that allows each server in a pod (multi-store, single directory cluster) to communicate with each other.

The cross-pod API needs to handle the following behaviors:

1. Sharing - allow users on one pod to share calendars with users on another.
2. Managed attachments - allow users on one pod to add attachments to events hosted on another.
3. Delegate assignments - allow delegate assignments to users on other pods to correctly allow those other users to see the assignments on their own principal resources.
4. Migration - allow user data to be moved from one pod to another, together with all sharing and delegate assignments.

Some other key requirements:

1. Must support pods using different versions of the software.
2. Must support load balancing of requests to pods with multiple hosts.

# Basic Design

The cross-pod code is located in the txdav.common.datastore.podding package.

The L{PoddingConduit} class handles the cross-pod requests and responses. The base API uses JSON to serialize commands as a set of "actions" with additional arguments. L{PoddingConduit} has a L{conduitRequestClass} class variable that defines how the requests are sent. The default implementation is L{ConduitRequest} which uses HTTP to send the request to another Pod. The L{ConduitResource} is an HTTP L{Resource} that handles the cross-pod HTTP request and channels the request data to the L{PoddingConduit} in the recipient pod. Thus two L{PoddingConduits} on different pods are "hooked-up" to each other over HTTP. For unit testing it is possible to hook up two L{PoddingConduits} to directly call each other, thus bypassing the need to setup any HTTP servers.

When a L{CommonDataStore} is created, it creates a L{PoddingConduit} object and assigns it to an instance variable. When a store detects that a cross-pod request is needed it will use the associated conduit.

The L{PoddingConduit} JSON request object contains an "action" member that is the name of the RPC call, together with "arguments" and "keywords" members representing the position arguments and keyword arguments of the RPC call. The request object also contains sufficient information to identify the target store object (the home user uid, home child resource id, object resource resource id, etc).

The L{PoddingConduit} JSON response object contains a "result" member that indicates whether the request succeeded (when set to the value "ok") or failed (when set to the value "exception"). When set to "ok", there will be a "value" member present with the result of the RPC call. When set to "exception" there will be a "class" member (whose name matches the class of the exception object raised) and a "details" member (whose value is the string representation of the raised exception). If an exception is returned as the response, the conduit on the sender's side will raise the named exception.

# External Store API

A L{CommonDataStore} makes use of the following key classes: L{CommonHome}, L{CommonHomeChild}, L{CommonObjectResource}. Each of those make calls to the store's underlying database (SQL) to store and retrieve their associated data.

For cross-pod support, there are a new set of derived classes: L{CommonHomeExternal}, L{CommonHomeChildExternal}, L{CommonObjectResourceExternal}. These classes override the methods that make calls to the database, and instead direct those calls to the store's conduit using the same API as the original method. The conduit then serializes the API call and sends it to the other pod, which deserializes the request, creates the matching "internal" store object and calls the appropriate method with the supplied arguments (note that the API has to distinguish the case of a call of a class method vs an instance method). In effect this implements a remote procedure call from a store object on one pod, to a store object on another.

The L{CommonHomeExternal}, L{CommonHomeChildExternal}, L{CommonObjectResourceExternal} are "chained" such that an L{CommonHomeExternal} will use L{CommonHomeChildExternal} as its home child class, and L{CommonHomeChildExternal} will use L{CommonObjectResourceExternal} as its object resource class (though for sharing this behavior can be different).

Note that the external classes typically exist without any associated data in the local store's database - i.e., they entirely represent objects in another pod - (though for sharing this behavior can be different).

The external store API is handled by the L{StoreAPIConduitMixin} which defines a set of methods on the L{PoddingConduit} class that implement the necessary RPC calls. For each RPC call there is one "send_XXX" and one "recv_XXX" method for the action "XXX". The "send" method is called by the external store object, and the "recv" method is called by the receiving pod's HTTP request handling resource. The L{StoreAPIConduitMixin} has a L{_make_simple_action} class method that can be used to programmatically create a set of "send_XXX" and "recv_XXX" methods for each RPC call. 

The L{StoreAPIConduitMixin} also contains specialized "send_XXX" and "recv_XXX" methods for some specific API calls that either don't use store objects directly or have more complex requirements. Other mixin classes add additional RPC calls for specific behaviors.

# Sharing API

When sharing a collection, the collection is identified via an ownerHome and a viewerHome. The owner is the sharer and the viewer is the sharee (when both are the same then the collection is the owner's own view of it). A BIND table is used to map a collection to a specific viewer home. There will always be one such entry for the viewer == owner case. When a sharee is added, there will be a new BIND entry for them.

For cross-pod sharing, we want to replicate the BIND table entries across pods so that each pod can quickly identify that shared collections from another pod exist, without the need to initiate cross-pod calls to all pods to query for possible shared collections. The owner and sharee of a collection are identifiable on each pod:

  1. Owners pod:
	- A HOME table entry for the sharee marked as status-external
	- A BIND table entry for the shared calendar referencing the sharee HOME entry
  2. Sharee's pod:
	- A HOME table entry for the owner marked as status-external
	- A BIND table entry for the owner's calendar referencing the owner HOME
		
Cross-pod sharing can then be split into two functional areas:

  1. Management of sharing invites (i.e., creating, updating, removing BIND rows, and handling notifications)
  2. Sharee accessing owner data (object resources, sync tokens etc)
	
For (1) a set of "invite" specific conduit APIs exist in the L{SharingInvitesConduitMixin}. The existing store classes are modified to spot when a viewer or owner home is "external" and in such cases will send a cross-pod request to the relevant pod.

For (2), the sharee's pod needs to direct store data requests to the owner pod for specific operations (anything that needs to access the owner data). Basically any request where the ownerHome() of the collection refers to an external home needs to be directed to the owner's pod. i.e., the server chosen for a cross-pod request must be based on the owner's server and not the viewer's server (since the request originates on the viewer's server).

