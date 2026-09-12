#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "l2mm/orderbook.hpp"

#include <memory>
#include <new>
#include <stdexcept>

namespace {

constexpr char capsule_name[] = "l2mm.Orderbook.v1";
struct PythonError {};
struct Decref {
    void operator()(PyObject* object) const { Py_XDECREF(object); }
};
using Owned = std::unique_ptr<PyObject, Decref>;

PyObject* checked(PyObject* object) {
    if (!object) throw PythonError{};
    return object;
}

template <typename Function>
PyObject* guarded(Function function) {
    try {
        return function();
    } catch (const PythonError&) {
        // The failing CPython call has already set the precise exception.
        return nullptr;
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::overflow_error& error) {
        PyErr_SetString(PyExc_OverflowError, error.what());
    } catch (const std::invalid_argument& error) {
        PyErr_SetString(PyExc_ValueError, error.what());
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "unknown native order-book exception");
    }
    return nullptr;
}

std::string unicode_string(PyObject* object) {
    Py_ssize_t size = 0;
    const char* bytes = PyUnicode_AsUTF8AndSize(object, &size);
    if (!bytes) throw PythonError{};
    return std::string(bytes, static_cast<std::size_t>(size));
}

PyObject* python_string(const std::string& value) {
    return checked(PyUnicode_FromStringAndSize(value.data(), static_cast<Py_ssize_t>(value.size())));
}

l2mm::Orderbook& get_book(PyObject* capsule) {
    auto* result = static_cast<l2mm::Orderbook*>(PyCapsule_GetPointer(capsule, capsule_name));
    if (!result) throw PythonError{};
    return *result;
}

void destroy_book(PyObject* capsule) {
    // Only this module creates capsules with this name and destructor.
    auto* book = static_cast<l2mm::Orderbook*>(PyCapsule_GetPointer(capsule, capsule_name));
    if (book) delete book;
    else PyErr_Clear();
}

l2mm::InputLevels input_levels(PyObject* object) {
    Owned sequence(checked(PySequence_Fast(object, "levels must be an iterable of price/quantity pairs")));
    const auto count = PySequence_Fast_GET_SIZE(sequence.get());
    l2mm::InputLevels result;
    result.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t index = 0; index < count; ++index) {
        Owned pair(checked(PySequence_Fast(PySequence_Fast_GET_ITEM(sequence.get(), index),
                                           "each level must be a price/quantity pair")));
        if (PySequence_Fast_GET_SIZE(pair.get()) != 2) {
            throw std::invalid_argument("each level must contain exactly price and quantity");
        }
        result.emplace_back(unicode_string(PySequence_Fast_GET_ITEM(pair.get(), 0)),
                            unicode_string(PySequence_Fast_GET_ITEM(pair.get(), 1)));
    }
    return result;
}

PyObject* optional_decimal(std::optional<l2mm::Scaled> value) {
    if (value) return python_string(l2mm::format_decimal(*value));
    Py_RETURN_NONE;
}

PyObject* book_state(const l2mm::Orderbook& book) {
    Owned result(checked(PyTuple_New(8)));
    PyTuple_SET_ITEM(result.get(), 0, optional_decimal(book.best_bid()));
    PyTuple_SET_ITEM(result.get(), 1, optional_decimal(book.best_ask()));
    PyTuple_SET_ITEM(result.get(), 2, optional_decimal(book.best_bid_qty()));
    PyTuple_SET_ITEM(result.get(), 3, optional_decimal(book.best_ask_qty()));
    PyTuple_SET_ITEM(result.get(), 4, checked(PyLong_FromSize_t(book.bid_count())));
    PyTuple_SET_ITEM(result.get(), 5, checked(PyLong_FromSize_t(book.ask_count())));
    PyObject* update_id = nullptr;
    if (book.last_update_id()) update_id = checked(PyLong_FromUnsignedLongLong(*book.last_update_id()));
    else { Py_INCREF(Py_None); update_id = Py_None; }
    PyTuple_SET_ITEM(result.get(), 6, update_id);
    PyTuple_SET_ITEM(result.get(), 7, checked(PyLong_FromUnsignedLongLong(book.sequence())));
    return result.release();
}

PyObject* create(PyObject*, PyObject* arguments) {
    return guarded([&]() -> PyObject* {
        PyObject *tick = nullptr, *step = nullptr;
        if (!PyArg_ParseTuple(arguments, "OO:create", &tick, &step)) throw PythonError{};
        auto book = std::make_unique<l2mm::Orderbook>(
            l2mm::Instrument::from_strings(unicode_string(tick), unicode_string(step)));
        Owned capsule(checked(PyCapsule_New(book.get(), capsule_name, destroy_book)));
        book.release();
        return capsule.release();
    });
}

PyObject* state(PyObject*, PyObject* capsule) {
    return guarded([&] { return book_state(get_book(capsule)); });
}

template <bool Snapshot>
PyObject* apply(PyObject*, PyObject* arguments) {
    return guarded([&]() -> PyObject* {
        PyObject *capsule = nullptr, *bids = nullptr, *asks = nullptr, *identifier = nullptr;
        if (!PyArg_ParseTuple(arguments, "OOOO:apply", &capsule, &bids, &asks, &identifier)) {
            throw PythonError{};
        }
        auto& book = get_book(capsule);
        const auto update_id = PyLong_AsUnsignedLongLong(identifier);
        if (PyErr_Occurred()) throw PythonError{};
        // Convert both sides and validate all levels before mutating native state.
        const auto bid_levels = input_levels(bids);
        const auto ask_levels = input_levels(asks);
        if constexpr (Snapshot) book.apply_snapshot(bid_levels, ask_levels, update_id);
        else book.apply_diff(bid_levels, ask_levels, update_id);
        return book_state(book);
    });
}

template <bool Bids>
PyObject* quantity(PyObject*, PyObject* arguments) {
    return guarded([&]() -> PyObject* {
        PyObject *capsule = nullptr, *price_text = nullptr;
        if (!PyArg_ParseTuple(arguments, "OO:quantity", &capsule, &price_text)) throw PythonError{};
        const auto& book = get_book(capsule);
        const auto price = l2mm::parse_decimal(unicode_string(price_text));
        return python_string(l2mm::format_decimal(Bids ? book.bid_quantity(price) : book.ask_quantity(price)));
    });
}

template <bool Bids>
PyObject* levels(PyObject*, PyObject* arguments) {
    return guarded([&]() -> PyObject* {
        PyObject* capsule = nullptr;
        Py_ssize_t count = 0;
        if (!PyArg_ParseTuple(arguments, "On:levels", &capsule, &count)) throw PythonError{};
        const auto& book = get_book(capsule);
        const auto limit = count > 0 ? static_cast<std::size_t>(count) : 0;
        const auto values = Bids ? book.bid_levels(limit) : book.ask_levels(limit);
        Owned result(checked(PyList_New(static_cast<Py_ssize_t>(values.size()))));
        for (std::size_t index = 0; index < values.size(); ++index) {
            Owned pair(checked(PyTuple_New(2)));
            PyTuple_SET_ITEM(pair.get(), 0, python_string(l2mm::format_decimal(values[index].first)));
            PyTuple_SET_ITEM(pair.get(), 1, python_string(l2mm::format_decimal(values[index].second)));
            PyList_SET_ITEM(result.get(), static_cast<Py_ssize_t>(index), pair.release());
        }
        return result.release();
    });
}

PyObject* state_hash(PyObject*, PyObject* capsule) {
    return guarded([&] { return python_string(get_book(capsule).state_hash()); });
}

PyObject* canonical_bytes(PyObject*, PyObject* capsule) {
    return guarded([&]() -> PyObject* {
        const auto bytes = get_book(capsule).canonical_bytes();
        return checked(PyBytes_FromStringAndSize(bytes.data(), static_cast<Py_ssize_t>(bytes.size())));
    });
}

PyObject* build_info(PyObject*, PyObject*) {
    return Py_BuildValue("{s:s,s:s,s:i,s:s}", "compiler", __VERSION__,
                         "build_type", L2MM_BUILD_TYPE, "api_version", 1,
                         "decimal_scale", "100000000");
}

PyMethodDef methods[] = {
    {"create", create, METH_VARARGS, "Create a checked fixed-point order book."},
    {"state", state, METH_O, "Return BBO, counts, update id, and sequence."},
    {"apply_snapshot", apply<true>, METH_VARARGS, "Replace the book and return its public state."},
    {"apply_diff", apply<false>, METH_VARARGS, "Apply absolute depth updates and return public state."},
    {"bid_quantity", quantity<true>, METH_VARARGS, "Return quantity at a bid price."},
    {"ask_quantity", quantity<false>, METH_VARARGS, "Return quantity at an ask price."},
    {"bid_levels", levels<true>, METH_VARARGS, "Return top bid levels, best first."},
    {"ask_levels", levels<false>, METH_VARARGS, "Return top ask levels, best first."},
    {"state_hash", state_hash, METH_O, "Hash the exact canonical Python-compatible book bytes."},
    {"canonical_bytes", canonical_bytes, METH_O, "Return the exact canonical book bytes."},
    {"build_info", build_info, METH_NOARGS, "Return compiler, build configuration, and API version."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_l2mm_book",
    "CPython binding for the checked C++17 L2 order book.",
    -1, methods, nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit__l2mm_book() { return PyModule_Create(&module); }
