#include "l2mm/orderbook.hpp"

#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void rejects(const std::function<void()>& action) {
    bool rejected = false;
    try { action(); }
    catch (const std::invalid_argument&) { rejected = true; }
    catch (const std::overflow_error&) { rejected = true; }
    check(rejected, "invalid input was not rejected");
}

void test_decimal() {
    check(l2mm::parse_decimal("74574.79000000") == 7457479000000LL, "exact decimal parse");
    check(l2mm::format_decimal(100) == "0.00000100", "fraction padding");
    check(l2mm::format_decimal(0) == "0.00000000", "zero formatting");
    check(l2mm::parse_decimal("92233720368.54775807") == std::numeric_limits<l2mm::Scaled>::max(), "maximum int64 parse");
    check(l2mm::format_decimal(std::numeric_limits<l2mm::Scaled>::max()) == "92233720368.54775807", "maximum int64 format");
    for (const auto* text : {"", "1", "1.0", "1.000000000", ".00000000", "-1.00000000", "-0.00000000", "+1.00000000", "1e0", "1.0000e000", "1.0000000 ", "NaN", "Infinity", "0x1.00000000", "1..0000000", "92233720368.54775808", "100000000000000000000000000000.00000000"}) {
        rejects([text] { l2mm::parse_decimal(text); });
    }
    rejects([] { l2mm::format_decimal(-1); });
}

void test_hash() {
    check(l2mm::sha256("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "SHA256 empty known answer");
    check(l2mm::sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "SHA256 abc known answer");
    check(l2mm::sha256(std::string(1000000, 'a')) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", "SHA256 million-a known answer");
}

void test_operations() {
    const auto instrument = l2mm::Instrument::from_strings("0.01000000", "0.00000001");
    l2mm::Orderbook book(instrument);
    check(book.canonical_bytes() == "{\"bids\":[],\"asks\":[],\"last_update_id\":null}", "empty canonical JSON");
    check(!book.best_bid() && !book.best_ask() && !book.is_crossed(), "empty best prices");
    book.apply_snapshot({{"99.00000000", "1.00000000"}, {"100.00000000", "2.00000000"}, {"100.00000000", "0.00000000"}},
                        {{"102.00000000", "3.00000000"}, {"101.00000000", "0.00000100"}}, 50);
    check(book.best_bid() == 10000000000LL && book.best_ask() == 10100000000LL, "sorted best prices");
    check(book.canonical_bytes() == "{\"bids\":[[\"100.00000000\",\"2.00000000\"],[\"99.00000000\",\"1.00000000\"]],\"asks\":[[\"101.00000000\",\"0.00000100\"],[\"102.00000000\",\"3.00000000\"]],\"last_update_id\":50}", "snapshot duplicate zero must skip, not erase");
    book.apply_diff({{"100.00000000", "0.00000000"}, {"90.00000000", "0.00000000"}}, {}, 51);
    check(book.bid_count() == 1 && book.best_bid() == 9900000000LL && book.sequence() == 1, "delete existing and absent");
    book.apply_diff({{"103.00000000", "1.00000000"}}, {}, 52);
    check(book.is_crossed() && book.sequence() == 2, "crossed book retained");
    book.apply_diff({{"103.00000000", "2.00000000"}, {"103.00000000", "0.00000000"}}, {}, 53);
    check(!book.is_crossed() && book.bid_count() == 1, "duplicate diff last write wins");
    book.apply_snapshot({}, {}, 60);
    check(book.bid_count() == 0 && book.ask_count() == 0 && book.sequence() == 0 && book.last_update_id() == 60, "resnapshot clears and resets");
}

void test_invalid_operations_are_atomic() {
    const auto instrument = l2mm::Instrument::from_strings("0.01000000", "0.00000100");
    l2mm::Orderbook book(instrument);
    book.apply_snapshot({{"100.00000000", "1.00000000"}}, {{"101.00000000", "2.00000000"}}, 100);
    book.apply_diff({}, {}, 101);
    const auto before = book.trace_json();
    for (const auto& invalid : l2mm::InputLevels{
        {"100.00100000", "1.00000000"}, {"100.00000000", "0.00000101"},
        {"100.00000000", "0.00000001"}, {"0.00000001", "1.00000000"},
        {"0.00000000", "1.00000000"}, {"-100.00000000", "1.00000000"},
        {"100.00000000", "-1.00000000"}, {"100.0", "1.00000000"},
        {"100.00000000", "92233720368.54775808"}}) {
        rejects([&] { book.apply_diff({{"99.00000000", "5.00000000"}}, {invalid}, 999); });
        check(book.trace_json() == before, "rejected diff mutated state");
        rejects([&] { book.apply_snapshot({{"99.00000000", "5.00000000"}}, {invalid}, 999); });
        check(book.trace_json() == before, "rejected snapshot mutated state");
    }
    rejects([] { l2mm::Instrument::from_strings("0.00000000", "0.00000001"); });
    rejects([] { l2mm::Instrument::from_strings("0.01000000", "0.00000000"); });
    rejects([] { l2mm::Orderbook invalid({-1, 1}); });
    rejects([] { l2mm::Orderbook invalid({1, -1}); });
}
}  // namespace

int main() {
    try {
        test_decimal();
        test_hash();
        test_operations();
        test_invalid_operations_are_atomic();
        std::cout << "Native orderbook checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native orderbook check failed: " << error.what() << '\n';
        return 1;
    }
}
